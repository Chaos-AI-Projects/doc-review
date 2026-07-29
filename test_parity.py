"""Anchor-parity test: renderer.py must produce identical block→line ranges
regardless of execution environment (CPython server-side vs Pyodide in-browser).

This test verifies the CANONICAL block ranges defined in ``parity_fixture.py``.
The same fixture is used by the Pyodide preview page's client-side parity
check — if ranges diverge, the spike is unsafe (risk of re-orphaning
comments; cf. 2026-07-22 comment-loss incident).

TDD: this test was written BEFORE the Pyodide integration so it can gate
the approach.
"""

from parity_fixture import EXPECTED_RANGES, PARITY_FIXTURE
from renderer import render_markdown_blocks


def test_parity_block_ranges():
    """Block→line ranges from render_markdown_blocks() match the canonical set."""
    blocks = render_markdown_blocks(PARITY_FIXTURE)
    ranges = [(b["start_line"], b["end_line"]) for b in blocks]
    assert ranges == EXPECTED_RANGES, (
        f"Block ranges diverged from canonical.\n"
        f"  Got:      {ranges}\n"
        f"  Expected: {EXPECTED_RANGES}"
    )


def test_parity_block_count():
    """Fixture produces exactly the expected number of blocks."""
    blocks = render_markdown_blocks(PARITY_FIXTURE)
    assert len(blocks) == len(EXPECTED_RANGES)


def test_parity_blocks_have_html():
    """Every block in the fixture produces non-empty HTML."""
    blocks = render_markdown_blocks(PARITY_FIXTURE)
    for b in blocks:
        assert b["html"].strip(), (
            f"Block at lines {b['start_line']}-{b['end_line']} has empty HTML"
        )


def test_parity_blocks_have_raw():
    """Every block in the fixture preserves its raw source text."""
    blocks = render_markdown_blocks(PARITY_FIXTURE)
    for b in blocks:
        assert b["raw"].strip(), (
            f"Block at lines {b['start_line']}-{b['end_line']} has empty raw text"
        )


def test_parity_ranges_are_contiguous():
    """Block ranges cover the entire document without gaps (except blank lines
    between blocks, which markdown-it does not assign to any block)."""
    blocks = render_markdown_blocks(PARITY_FIXTURE)
    for i in range(1, len(blocks)):
        prev_end = blocks[i - 1]["end_line"]
        curr_start = blocks[i]["start_line"]
        # Current block starts at or after the previous block ends.
        assert curr_start > prev_end, (
            f"Block overlap: block {i-1} ends at {prev_end}, "
            f"block {i} starts at {curr_start}"
        )
