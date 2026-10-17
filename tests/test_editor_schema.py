"""The server cannot run ProseMirror, so it keeps its own count of the
schema: its version number, and which nodes are leaves for position math."""

from __future__ import annotations

from api.services.prosemirror import (
    LEAF_TYPES,
    SCHEMA_VERSION,
    content_size,
    insert_after,
    to_plain_text,
)
from tests.editor_fixtures import load, manifest


def test_the_server_and_client_share_one_schema_version() -> None:
    assert SCHEMA_VERSION == manifest()["version"]


def test_every_leaf_in_the_client_schema_counts_as_one_position() -> None:
    leaves = {
        name
        for name, spec in manifest()["nodes"].items()
        if spec["leaf"] and name != "text"
    }
    assert leaves <= LEAF_TYPES


def test_positions_agree_with_prosemirror_on_a_version_2_document() -> None:
    """The fixture's size is ProseMirror's own; page breaks and tables in it
    would shift every later position if the server miscounted them."""
    edit = load("v2-edit.json")

    assert content_size(edit["doc"]) == edit["size"]


def test_an_ai_insert_lands_after_a_page_break_and_a_table() -> None:
    edit = load("v2-edit.json")

    inserted = insert_after(edit["doc"], None, "Appended")

    assert inserted.step["from"] == edit["size"]
    assert [block["type"] for block in inserted.content["content"][-3:]] == [
        "pageBreak",
        "table",
        "paragraph",
    ]


def test_new_blocks_read_sensibly_in_a_prompt() -> None:
    text = to_plain_text(load("every-node.json")).text

    assert "- [x] Done" in text
    assert "- [ ] To do" in text
    assert "  - [ ] Nested task" in text
    assert "Merged across both" in text
    # A page break reads as a rule, between what came before and after it.
    assert text.index("---") < text.index("After the break")
