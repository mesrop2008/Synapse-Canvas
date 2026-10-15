"""Builds what is sent to the provider from the mode, the user's instruction,
the document title, the document itself and passages of the workspace's
sources. The wording is in api/i18n/*/ai.json, in the language the user's
client asks for.

The title, the document and the sources are untrusted: anyone in the
workspace wrote the first two, and the sources came from outside it. Any of
them may contain text posing as instructions. They are fenced in tags the
system instruction declares to be data. That mitigates prompt injection; it
does not solve it, which is one reason output is only ever a proposal the
user has to accept."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from api.core import i18n
from api.core.exceptions import AppError, ErrorCode
from api.llm.base import CHARS_PER_TOKEN, LLMRequest
from api.models.enums import AIQueryMode
from api.services.prosemirror import to_plain_text

# Punctuation only, so the same in every language.
OMITTED = "\n[…]\n"

_TAG = re.compile(
    r"<(/?)(title|document|selection|sources|source|instruction)\b", re.IGNORECASE
)


class SelectionError(AppError):
    status_code = 422
    detail = "Select the text to rewrite"
    code = ErrorCode.AI_SELECTION_INVALID


@dataclass(frozen=True, slots=True)
class Source:
    """A retrieved passage, shown to the model as `[marker]`. The model is
    given no filename, page or link to repeat: a citation is resolved from
    the marker on the server."""

    marker: int
    text: str


@dataclass(frozen=True, slots=True)
class DocumentContext:
    before: str
    selection: str = ""
    after: str = ""
    truncated: bool = False


def _fence(text: str) -> str:
    # Stops untrusted text closing our tags early and opening its own.
    return _TAG.sub(r"&lt;\1\2", text)


def _keep_ends(text: str, budget: int, head_share: float) -> tuple[str, bool]:
    """Cut from the middle: the opening says what a document is about, and the
    end is usually what the user is working on."""
    if len(text) <= budget:
        return text, False
    head = int(budget * head_share)
    tail = budget - head
    return text[:head] + OMITTED + (text[-tail:] if tail else ""), True


def build_context(
    content: dict[str, Any],
    mode: AIQueryMode,
    selection_from: int | None,
    selection_to: int | None,
    budget_tokens: int,
) -> DocumentContext:
    plain = to_plain_text(content)
    text = plain.text
    budget = budget_tokens * CHARS_PER_TOKEN

    if mode is AIQueryMode.REWRITE:
        if selection_from is None or selection_to is None:
            raise SelectionError()
        start = plain.offset(selection_from)
        end = plain.offset(selection_to, forward=False)
        if end <= start or not text[start:end].strip():
            raise SelectionError()
        return _around_selection(text, start, end, budget)

    if mode is AIQueryMode.CONTINUE:
        cursor = len(text) if selection_to is None else plain.offset(
            selection_to, forward=False
        )
        before, truncated = _keep_ends(text[:cursor], budget, head_share=0.25)
        return DocumentContext(before=before, truncated=truncated)

    whole, truncated = _keep_ends(text, budget, head_share=0.5)
    return DocumentContext(before=whole, truncated=truncated)


def _around_selection(text: str, start: int, end: int, budget: int) -> DocumentContext:
    """The selection is never cut. What room is left goes to the start of the
    document and to the text either side of the selection, in that order."""
    selection = text[start:end]
    if len(selection) > budget:
        raise SelectionError(
            "The selection is longer than the AI can rewrite at once",
            code=ErrorCode.AI_CONTEXT_TOO_LONG,
        )

    before, after = text[:start], text[end:]
    room = budget - len(selection)
    if len(before) + len(after) <= room:
        return DocumentContext(before, selection, after)

    after_room = min(len(after), room // 3)
    before_room = room - after_room
    if before_room > len(before):
        after_room = min(len(after), after_room + before_room - len(before))
        before_room = len(before)

    kept_before, _ = _keep_ends(before, before_room, head_share=0.4)
    kept_after = after[:after_room] + (OMITTED if after_room < len(after) else "")
    return DocumentContext(kept_before, selection, kept_after, truncated=True)


def sources_section(sources: Sequence[Source]) -> str:
    """Uploaded files are adversarial in a way the document is not: a PDF
    can carry text written to steer whatever model reads it. Each passage is
    fenced so it cannot close its tag and speak outside it, and the system
    instruction says <sources> is evidence, never instructions. This narrows
    prompt injection; it does not prevent it -- a model can still be talked
    round -- so answers stay proposals, and citations are resolved here
    rather than taken on the model's word."""
    if not sources:
        return ""
    items = "\n".join(
        f'<source marker="[{source.marker}]">\n{_fence(source.text)}\n</source>'
        for source in sources
    )
    return f"<sources>\n{items}\n</sources>"


def build_prompt(
    *,
    mode: AIQueryMode,
    instruction: str,
    title: str,
    context: DocumentContext,
    max_output_tokens: int,
    locale: i18n.Locale = i18n.DEFAULT_LOCALE,
    sources: Sequence[Source] = (),
) -> LLMRequest:
    mark = i18n.text(locale, "ai.prompt.selectionMark")
    task_key = mode.value
    if mode is AIQueryMode.ASK and sources:
        task_key = "askWithSources"
    task = i18n.text(locale, f"ai.prompt.tasks.{task_key}", mark=mark)
    default = i18n.text(locale, f"ai.prompt.defaults.{mode.value}")

    sections = [i18n.text(locale, "ai.prompt.task", task=task)]
    if sources:
        sections.append(i18n.text(locale, "ai.prompt.cite"))
    # Answering from the sources alone, the document is left out so it
    # cannot stand in for them.
    if mode is not AIQueryMode.SOURCES:
        document = context.before
        if mode is AIQueryMode.REWRITE:
            document += mark + context.after
        sections += [
            f"<title>{_fence(title)}</title>",
            f"<document>\n{_fence(document)}\n</document>",
        ]
        if mode is AIQueryMode.REWRITE:
            sections.append(f"<selection>\n{_fence(context.selection)}\n</selection>")
    sections.append(sources_section(sources))
    sections.append(
        f"<instruction>\n{_fence(instruction.strip() or default)}\n</instruction>"
    )

    return LLMRequest(
        system=i18n.text(locale, "ai.prompt.system"),
        user="\n\n".join(section for section in sections if section),
        max_output_tokens=max_output_tokens,
        locale=locale,
        source_count=len(sources),
    )
