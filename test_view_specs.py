"""Tests for the Python render-spec builders (#451).

These cases are a direct port of the spec-builder half of ``test_spa_nav.js``.
The JS copies they replace are deleted, so this file is now the *only*
specification of the row/TOC/header markup shape — which is the whole point of
the port: the Jinja server render and the Pyodide soft swap both consume these
functions, so they cannot drift.

The spec keys are camelCase on purpose: these dicts cross the WASM bridge and
are consumed verbatim by DOM code in ``static/app.js``.
"""

import json
from pathlib import Path

import pytest

from view_specs import (
    header_fields,
    line_label,
    row_class,
    source_row_specs,
    toc_item_specs,
)

BLOCKS = [
    {"start_line": 1, "end_line": 1, "html": "<h1>T</h1>"},
    {"start_line": 3, "end_line": 5, "html": "<p>body</p>"},
]


class TestRowClass:
    def test_plain_row_class(self):
        assert row_class(0) == "source-line"

    def test_commented_row_class(self):
        assert row_class(2) == "source-line has-comments"


class TestLineLabel:
    def test_single_line_label(self):
        assert line_label({"start_line": 4, "end_line": 4}) == "4"

    def test_multi_line_label(self):
        assert line_label({"start_line": 4, "end_line": 7}) == "4-7"


class TestSourceRowSpecs:
    def test_specs_carry_ids_classes_labels_html_and_counts(self):
        assert source_row_specs(BLOCKS, {"3": [{"id": 1}, {"id": 2}]}) == [
            {
                "id": "L1",
                "rowClass": "source-line",
                "startLine": 1,
                "endLine": 1,
                "label": "1",
                "html": "<h1>T</h1>",
                "commentCount": 0,
            },
            {
                "id": "L3",
                "rowClass": "source-line has-comments",
                "startLine": 3,
                "endLine": 5,
                "label": "3-5",
                "html": "<p>body</p>",
                "commentCount": 2,
            },
        ]

    def test_no_comment_data_marks_no_rows(self):
        specs = source_row_specs(BLOCKS, None)
        assert [s["rowClass"] for s in specs] == ["source-line", "source-line"]

    def test_no_blocks_no_rows(self):
        assert source_row_specs(None, {}) == []

    def test_int_and_str_comment_keys_agree(self):
        """The server passes int block keys, JSON hands back str keys — both
        must produce identical specs or a soft swap would drop the markers."""
        assert source_row_specs(BLOCKS, {3: [{"id": 1}]}) == source_row_specs(
            BLOCKS, {"3": [{"id": 1}]}
        )

    def test_anchor_id_is_the_block_start_line(self):
        """The ``id="L{start_line}"`` contract comments are anchored to."""
        assert [s["id"] for s in source_row_specs(BLOCKS, {})] == ["L1", "L3"]


class TestTocItemSpecs:
    def test_items_keep_level_classes_and_line_links(self):
        assert toc_item_specs(
            [
                {"level": 1, "text": "Title", "start_line": 1},
                {"level": 2, "text": "Sub", "start_line": 9},
            ]
        ) == [
            {"className": "toc-item toc-level-1", "href": "#L1", "text": "Title"},
            {"className": "toc-item toc-level-2", "href": "#L9", "text": "Sub"},
        ]

    def test_empty_toc_no_items(self):
        assert toc_item_specs([]) == []

    def test_missing_toc_no_items(self):
        assert toc_item_specs(None) == []


class TestHeaderFields:
    @pytest.fixture
    def fields(self):
        return header_fields(
            {
                "path": "kb/wiki/log.md",
                "file_id": "0123456789abcdef0123456789abcdef",
            }
        )

    def test_title_is_the_path(self, fields):
        assert fields["title"] == "kb/wiki/log.md"

    def test_file_id_truncated_to_12_chars_plus_ellipsis(self, fields):
        assert fields["fileIdLabel"] == "0123456789ab\u2026"

    def test_document_title_follows_the_path(self, fields):
        assert fields["documentTitle"] == "doc-review \u2014 kb/wiki/log.md"

    def test_comment_form_carries_the_file_id(self, fields):
        assert fields["formFileId"] == "0123456789abcdef0123456789abcdef"

    def test_comment_form_carries_the_path(self, fields):
        assert fields["formPath"] == "kb/wiki/log.md"


class TestPyodideBridgeContract:
    """The client calls these builders across the WASM bridge as
    ``json.dumps(fn(*json.loads(arg)))`` (see ``callSpecBuilder`` in
    view.html).  Exercise that exact marshalling: JSON stringifies int dict
    keys and drops ``undefined``, so a builder that only works on native
    Python inputs would break only in the browser.
    """

    @staticmethod
    def _via_bridge(fn, args):
        return json.loads(json.dumps(fn(*json.loads(json.dumps(args)))))

    def test_row_specs_survive_the_round_trip(self):
        assert self._via_bridge(
            source_row_specs, [BLOCKS, {3: [{"id": 1}]}]
        ) == source_row_specs(BLOCKS, {3: [{"id": 1}]})

    def test_row_specs_accept_the_js_null_defaults(self):
        """app.js passes ``blocks || null`` / ``commentsByBlock || null``."""
        assert self._via_bridge(source_row_specs, [None, None]) == []

    def test_toc_specs_survive_the_round_trip(self):
        toc = [{"level": 2, "text": "Sub", "start_line": 9}]
        assert self._via_bridge(toc_item_specs, [toc]) == toc_item_specs(toc)

    def test_header_fields_survive_the_round_trip(self):
        data = {"path": "a.md", "file_id": "f" * 64}
        assert self._via_bridge(header_fields, [data]) == header_fields(data)


class TestNoJsDuplicate:
    """Anti-drift: the ported builders must not survive as a second JS copy."""

    @pytest.fixture
    def nav_logic_src(self):
        return (Path(__file__).parent / "static" / "nav_logic.js").read_text()

    @pytest.mark.parametrize(
        "name", ["sourceRowSpecs", "tocItemSpecs", "headerFields", "rowClass", "lineLabel"]
    )
    def test_ported_builder_removed_from_js(self, name, nav_logic_src):
        assert name not in nav_logic_src, (
            f"{name} was ported to view_specs.py but a JS copy remains — "
            "the two will drift"
        )

    @pytest.mark.parametrize(
        "name",
        ["shouldIntercept", "popstateAction", "lineAnchorId", "viewUrl", "apiSourceUrl"],
    )
    def test_routing_half_stays_in_js(self, name, nav_logic_src):
        """Boot-ordering logic must NOT move: it decides what happens before
        the Pyodide renderer is warm."""
        assert name in nav_logic_src
