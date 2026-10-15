from __future__ import annotations

import re

from api.embeddings.base import conservative_token_count
from api.services.chunking import chunk_text
from api.services.extraction import PAGE_BREAK

SENTENCES = [f"Sentence number {i} says something about topic {i % 7}." for i in range(120)]


def chunks(text: str, page_starts: list[int] | None = None, **sizes: int):
    options = {"target_tokens": 60, "overlap_tokens": 15, "max_tokens": 80, **sizes}
    return chunk_text(
        text, page_starts or [], count_tokens=conservative_token_count, **options
    )


def test_every_passage_is_an_exact_slice_within_the_cap() -> None:
    text = "\n\n".join(" ".join(SENTENCES[i : i + 6]) for i in range(0, 120, 6))

    drafts = chunks(text)

    assert len(drafts) > 5
    assert [d.index for d in drafts] == list(range(len(drafts)))
    for draft in drafts:
        assert draft.content == text[draft.char_start : draft.char_end]
        assert draft.token_count == conservative_token_count(draft.content)
        assert draft.token_count <= 80


def test_passages_end_on_sentence_boundaries_and_overlap() -> None:
    text = " ".join(SENTENCES)

    # Room for one sentence (about 17 tokens) of overlap.
    drafts = chunks(text, overlap_tokens=20)

    for draft in drafts:
        assert draft.content.startswith("Sentence number")
        assert draft.content.endswith(".")
    for before, after in zip(drafts, drafts[1:], strict=False):
        assert after.char_start < before.char_end, "consecutive passages overlap"
        assert after.char_start > before.char_start, "and always move forward"


def test_without_overlap_the_passages_tile_the_text() -> None:
    text = " ".join(SENTENCES)

    drafts = chunks(text, overlap_tokens=0)

    covered = " ".join(d.content for d in drafts)
    assert covered == text


def test_text_without_punctuation_is_cut_between_words() -> None:
    text = " ".join(f"word{i}" for i in range(2000))

    drafts = chunks(text)

    words = set(re.findall(r"\S+", text))
    for draft in drafts:
        assert set(draft.content.split()) <= words
        assert draft.token_count <= 80


def test_a_single_huge_word_is_cut_to_fit() -> None:
    blob = "Q" * 5000

    drafts = chunks(f"Before. {blob} After.")

    assert "".join(d.content for d in drafts if "Q" in d.content).count("Q") >= 5000
    assert all(d.token_count <= 80 for d in drafts)


def test_each_passage_knows_the_page_it_starts_on() -> None:
    pages = [" ".join(SENTENCES[i : i + 20]) for i in range(0, 60, 20)]
    starts = []
    position = 0
    for page in pages:
        starts.append(position)
        position += len(page) + len(PAGE_BREAK)
    text = PAGE_BREAK.join(pages)

    drafts = chunks(text, starts)

    assert {d.page_number for d in drafts} == {1, 2, 3}
    for draft in drafts:
        expected = max(i + 1 for i, start in enumerate(starts) if start <= draft.char_start)
        assert draft.page_number == expected


def test_a_passage_the_tokenizer_counts_higher_as_a_whole_is_halved() -> None:
    """A tokenizer need not add up: here every passage of more than one unit
    costs extra, as special tokens would."""

    def count(text: str) -> int:
        return conservative_token_count(text) + (40 if "\n\n" in text else 0)

    text = "\n\n".join(" ".join(SENTENCES[i : i + 2]) for i in range(0, 40, 2))

    drafts = chunk_text(
        text, [], count_tokens=count, target_tokens=60, overlap_tokens=0, max_tokens=60
    )

    assert all(d.token_count <= 60 for d in drafts)


def test_blank_text_gives_no_passages() -> None:
    assert chunks("  \n\n \t ") == []
