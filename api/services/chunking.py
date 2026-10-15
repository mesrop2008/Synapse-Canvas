"""Splits extracted text into passages for embedding.

A passage is always an exact slice of the text, so `char_start`/`char_end`
point a citation at what was embedded. Text is cut at the coarsest boundary
that fits -- paragraph, then sentence, then line, then word -- and only a
single "word" longer than a passage (a base64 blob, a URL run together) is
cut in the middle."""

from __future__ import annotations

import bisect
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass

TokenCounter = Callable[[str], int]

_PARAGRAPH_BREAK = re.compile(r"\n[ \t\r\f\v]*\n\s*")
# After terminal punctuation and any closing quotes or brackets.
_SENTENCE_END = re.compile(r"(?<=[.!?…。！？])[\"'»”’)\]]*\s+")
_LINE_BREAK = re.compile(r"\s*\n\s*")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    index: int
    content: str
    char_start: int
    char_end: int
    page_number: int | None
    token_count: int


@dataclass(frozen=True, slots=True)
class _Unit:
    start: int
    end: int
    tokens: int


def _pieces(
    text: str, start: int, end: int, separator: re.Pattern[str]
) -> Iterator[tuple[int, int]]:
    """Non-blank stretches of text[start:end] between separators, trimmed."""
    position = start
    for match in separator.finditer(text, start, end):
        yield from _trimmed(text, position, match.start())
        position = match.end()
    yield from _trimmed(text, position, end)


def _trimmed(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start < end:
        yield start, end


class _Splitter:
    def __init__(self, text: str, count: TokenCounter, limit: int) -> None:
        self.text = text
        self.count = count
        self.limit = limit

    def units(self) -> list[_Unit]:
        units: list[_Unit] = []
        for start, end in _pieces(self.text, 0, len(self.text), _PARAGRAPH_BREAK):
            units.extend(self._fit(start, end, (_SENTENCE_END, _LINE_BREAK, _SPACE)))
        return units

    def _fit(
        self, start: int, end: int, separators: tuple[re.Pattern[str], ...]
    ) -> Iterator[_Unit]:
        tokens = self.count(self.text[start:end])
        if tokens <= self.limit:
            yield _Unit(start, end, tokens)
            return
        if not separators:
            yield from self._cut(start, end)
            return
        pieces = list(_pieces(self.text, start, end, separators[0]))
        if len(pieces) == 1:
            yield from self._fit(start, end, separators[1:])
            return
        for piece_start, piece_end in pieces:
            yield from self._fit(piece_start, piece_end, separators[1:])

    def _cut(self, start: int, end: int) -> Iterator[_Unit]:
        """The longest prefix that fits, found by bisection, until none is left."""
        while start < end:
            low, high = start + 1, end
            while low < high:
                middle = (low + high + 1) // 2
                if self.count(self.text[start:middle]) <= self.limit:
                    low = middle
                else:
                    high = middle - 1
            yield _Unit(start, low, self.count(self.text[start:low]))
            start = low


def chunk_text(
    text: str,
    page_starts: list[int],
    *,
    count_tokens: TokenCounter,
    target_tokens: int,
    overlap_tokens: int,
    max_tokens: int,
) -> list[ChunkDraft]:
    """Packs units up to `target_tokens`, starting each passage with up to
    `overlap_tokens` of the end of the one before. No passage exceeds
    `max_tokens` by `count_tokens`."""
    target = min(target_tokens, max_tokens)
    units = _Splitter(text, count_tokens, target).units()
    spans: list[tuple[int, int]] = []

    first = 0
    while first < len(units):
        last = first
        total = units[first].tokens
        while last + 1 < len(units) and total + units[last + 1].tokens <= target:
            last += 1
            total += units[last].tokens
        spans.append((first, last))
        if last + 1 >= len(units):
            break
        # Step back over trailing units that fit in the overlap, but always
        # move forward.
        following = last + 1
        carried = 0
        while (
            following - 1 > first
            and carried + units[following - 1].tokens <= overlap_tokens
        ):
            following -= 1
            carried += units[following].tokens
        first = following

    drafts: list[ChunkDraft] = []
    for first, last in spans:
        drafts.extend(
            _verified(
                text, units[first : last + 1], count_tokens, max_tokens, page_starts
            )
        )
    return [
        ChunkDraft(
            index, d.content, d.char_start, d.char_end, d.page_number, d.token_count
        )
        for index, d in enumerate(drafts)
    ]


def _verified(
    text: str,
    units: list[_Unit],
    count_tokens: TokenCounter,
    max_tokens: int,
    page_starts: list[int],
) -> list[ChunkDraft]:
    """Units are counted one by one; the whole passage is counted once more,
    since a tokenizer need not add up. One over the cap is halved."""
    start, end = units[0].start, units[-1].end
    content = text[start:end]
    tokens = count_tokens(content)
    if tokens > max_tokens and len(units) > 1:
        half = len(units) // 2
        return _verified(
            text, units[:half], count_tokens, max_tokens, page_starts
        ) + _verified(text, units[half:], count_tokens, max_tokens, page_starts)
    page = bisect.bisect_right(page_starts, start) if page_starts else None
    return [ChunkDraft(0, content, start, end, page, tokens)]
