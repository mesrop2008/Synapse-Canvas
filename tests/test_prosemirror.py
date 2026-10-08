"""The server-side ProseMirror helpers. Their positions and steps were also
checked against prosemirror-model and prosemirror-transform while writing
them; these pin the behaviour down."""

from __future__ import annotations

from typing import Any

from api.services.prosemirror import (
    content_size,
    insert_after,
    paragraphs,
    replace_selection,
    snap_selection,
    to_plain_text,
)


def text(value: str, *marks: str) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": value}
    if marks:
        node["marks"] = [{"type": mark} for mark in marks]
    return node


def para(*children: dict[str, Any]) -> dict[str, Any]:
    return {"type": "paragraph", "content": list(children)}


def doc(*blocks: dict[str, Any]) -> dict[str, Any]:
    return {"type": "doc", "content": list(blocks)}


RICH = doc(
    {"type": "heading", "attrs": {"level": 2}, "content": [text("Notes 😀")]},
    para(text("Plain "), text("bold", "bold"), {"type": "hardBreak"}, text("next")),
    {
        "type": "bulletList",
        "content": [
            {"type": "listItem", "content": [para(text("one"))]},
            {"type": "listItem", "content": [para(text("two"))]},
        ],
    },
)


def test_plain_text_keeps_structure_a_model_can_read() -> None:
    assert to_plain_text(RICH).text == (
        "## Notes 😀\n\nPlain bold\nnext\n\n- one\n\n- two"
    )


def test_positions_count_utf16_units_like_the_editor() -> None:
    # The emoji is two units in JavaScript, so the heading is 10 long, not 9.
    assert content_size(doc(RICH["content"][0])) == len("Notes ") + 2 + 2
    plain = to_plain_text(RICH)
    heading_end = 1 + len("Notes ") + 2
    assert plain.text[plain.offset(1) : plain.offset(heading_end, forward=False)] == (
        "Notes 😀"
    )


def test_paragraphs_split_on_blank_lines_and_break_on_single_ones() -> None:
    assert paragraphs("One\nline\n\n\nTwo\n") == [
        para(text("One"), {"type": "hardBreak"}, text("line")),
        para(text("Two")),
    ]


def test_an_inline_rewrite_keeps_marks_around_it_and_merges_like_prosemirror() -> None:
    plain = doc(para(text("ab"), text("cd", "bold"), text("ef")))

    edit = replace_selection(plain, 3, 5, "XY")

    assert edit is not None
    assert edit.content == doc(para(text("abXYef")))
    assert edit.step == {
        "stepType": "replace",
        "from": 3,
        "to": 5,
        "slice": {"content": [text("XY")]},
    }


def test_an_inline_rewrite_keeps_the_whitespace_that_was_selected() -> None:
    edit = replace_selection(doc(para(text("a big dog"))), 2, 7, "  large  ")

    assert edit is not None
    assert edit.content == doc(para(text("a large dog")))


def test_a_rewrite_across_blocks_replaces_whole_top_level_blocks() -> None:
    paragraph_start = 1 + len("Notes ") + 2 + 1
    start = paragraph_start + 3  # inside "Plain"
    end = content_size(RICH) - 3  # inside "two"

    assert snap_selection(RICH, start, end) == (paragraph_start, content_size(RICH))
    edit = replace_selection(RICH, start, end, "Merged.")

    assert edit is not None
    assert edit.content == doc(RICH["content"][0], para(text("Merged.")))


def test_insert_after_lands_after_the_top_level_block_not_inside_a_list() -> None:
    inside_one = content_size(doc(*RICH["content"][:2])) + 4

    edit = insert_after(RICH, inside_one, "Reply.")

    assert edit.content["content"][-1] == para(text("Reply."))
    assert edit.step["from"] == edit.step["to"] == content_size(RICH)


def test_insert_into_an_empty_document() -> None:
    edit = insert_after(doc(), None, "First words.")

    assert edit.content == doc(para(text("First words.")))
    assert edit.step == {
        "stepType": "replace",
        "from": 0,
        "to": 0,
        "slice": {"content": [para(text("First words."))]},
    }
