"""Tests for the markdown block renderer."""

import re

from renderer import _sanitize_html, extract_toc, render_markdown_blocks


# ── Block-level rendering ────────────────────────────────────────────────


def test_basic_blocks():
    """Multiple lines without separators form a single paragraph block."""
    source = "line one\nline two\nline three"
    result = render_markdown_blocks(source)
    assert len(result) == 1
    assert result[0]["start_line"] == 1
    assert result[0]["end_line"] == 3
    assert "line one" in result[0]["html"]
    assert "line two" in result[0]["html"]
    assert "line three" in result[0]["html"]


def test_separated_paragraphs():
    """Blank-line separated paragraphs produce separate blocks."""
    source = "first paragraph\n\nsecond paragraph"
    result = render_markdown_blocks(source)
    assert len(result) == 2
    assert result[0]["start_line"] == 1
    assert result[0]["end_line"] == 1
    assert result[1]["start_line"] == 3
    assert result[1]["end_line"] == 3


def test_empty_source():
    result = render_markdown_blocks("")
    assert len(result) == 1
    assert result[0]["start_line"] == 1
    assert result[0]["raw"] == ""


def test_markdown_bold():
    result = render_markdown_blocks("**bold text**")
    assert "<strong>bold text</strong>" in result[0]["html"]


def test_markdown_inline_code():
    result = render_markdown_blocks("`code`")
    assert "<code>code</code>" in result[0]["html"]


def test_heading_rendered():
    result = render_markdown_blocks("# Heading")
    assert "<h1>" in result[0]["html"]
    assert "Heading" in result[0]["html"]


def test_preserves_raw():
    source = "**bold** and `code`"
    result = render_markdown_blocks(source)
    assert result[0]["raw"] == source


# ── TDD: list rendering ─────────────────────────────────────────────────


def test_two_consecutive_bullets_one_ul():
    """Two consecutive `- bullet` lines render as ONE <ul> with two <li>."""
    source = "- item one\n- item two"
    result = render_markdown_blocks(source)
    assert len(result) == 1, "Two consecutive bullets should be one block"
    html = result[0]["html"]
    assert html.count("<ul>") == 1, "Should produce exactly one <ul>"
    assert html.count("<li>") == 2, "Should have two <li> items"
    assert "item one" in html
    assert "item two" in html


def test_wrapped_list_item_stays_in_one_li():
    """A wrapped list item stays inside one <li>."""
    source = "- first line of item\n  continuation of item\n- second item"
    result = render_markdown_blocks(source)
    assert len(result) == 1, "Entire list should be one block"
    html = result[0]["html"]
    assert html.count("<li>") == 2, "Should have two <li> items"
    assert "first line of item" in html
    assert "continuation of item" in html
    assert "second item" in html


# ── TDD: table rendering ────────────────────────────────────────────────


def test_table_renders_as_one_table():
    """A markdown table renders as one <table> block."""
    source = "| A | B |\n|---|---|\n| 1 | 2 |"
    result = render_markdown_blocks(source)
    assert len(result) == 1, "Table should be one block"
    html = result[0]["html"]
    assert "<table>" in html
    assert "<th>" in html or "<th" in html
    assert "<td>" in html or "<td" in html


def test_column_alignment_survives_sanitizing():
    """A `:---:` delimiter row must reach the browser.

    markdown-it emits column alignment as `style="text-align:…"`, which the
    sanitizer drops because `style` is whitelisted nowhere.  The alignment is
    carried across as a class instead, so the sanitizer keeps its "no style
    attribute anywhere" invariant and the column still renders aligned."""
    source = "| A | B | C |\n|:--|:-:|--:|\n| 1 | 2 | 3 |"
    html = render_markdown_blocks(source)[0]["html"]
    assert "style=" not in html, "the sanitizer must still emit no style attribute"
    for side in ("left", "center", "right"):
        assert html.count(f'class="align-{side}"') == 2, (
            f"the {side}-aligned column needs the class on its th and its td"
        )


def test_a_default_column_gets_no_alignment_class():
    """`|---|` states no alignment, so the cell must not claim one: a class on
    every cell would override whatever the container decides."""
    source = "| A |\n|---|\n| 1 |"
    html = render_markdown_blocks(source)[0]["html"]
    assert "align-" not in html


def test_sanitizer_translates_only_a_bare_text_align_on_a_cell():
    """The translation is a whitelist of three literal values on two tags, not
    a style parser.  Anything else — a second declaration smuggled in beside
    the alignment, a value outside the three, or the same style on another
    tag — is dropped whole, exactly as `style` was before."""
    hostile = (
        '<td style="text-align:left;background:url(javascript:alert(1))">x</td>'
        '<th style="text-align:justify">y</th>'
        '<td style="text-align:expression(alert(1))">z</td>'
        '<p style="text-align:center">w</p>'
    )
    out = _sanitize_html(hostile)
    assert "style=" not in out
    assert "align-" not in out
    assert "background" not in out


# ── TDD: blocks expose line ranges ──────────────────────────────────────


def test_blocks_expose_line_ranges():
    """Each block exposes start_line and end_line covering its source lines."""
    source = "# Heading\n\n- one\n- two\n\nParagraph."
    result = render_markdown_blocks(source)
    assert len(result) == 3
    # Heading: line 1
    assert result[0]["start_line"] == 1
    assert result[0]["end_line"] == 1
    # List: lines 3-5 (markdown-it includes trailing blank in list range)
    assert result[1]["start_line"] == 3
    assert result[1]["end_line"] == 5
    # Paragraph: line 6
    assert result[2]["start_line"] == 6
    assert result[2]["end_line"] == 6


# ── TDD: TOC ────────────────────────────────────────────────────────────


def test_toc_lists_all_headings():
    """TOC lists all headings with correct levels and anchors."""
    source = "# Title\n\nSome text.\n\n## Section A\n\nMore text.\n\n### Subsection\n\n## Section B"
    toc = extract_toc(source)
    assert len(toc) == 4
    assert toc[0] == {"level": 1, "text": "Title", "start_line": 1}
    assert toc[1] == {"level": 2, "text": "Section A", "start_line": 5}
    assert toc[2] == {"level": 3, "text": "Subsection", "start_line": 9}
    assert toc[3] == {"level": 2, "text": "Section B", "start_line": 11}


def test_toc_empty_for_no_headings():
    """TOC is empty when there are no headings."""
    toc = extract_toc("Just a paragraph.\n\nAnother one.")
    assert toc == []


# ── Sanitization (same behavior as before) ───────────────────────────────


def test_script_tag_stripped():
    result = render_markdown_blocks('<script>alert("xss")</script>')
    assert "<script>" not in result[0]["html"]
    assert "</script>" not in result[0]["html"]


def test_img_onerror_stripped():
    result = render_markdown_blocks('<img src=x onerror=alert(1)>')
    # With html: False, markdown-it escapes the tag entirely — no live <img>.
    assert "<img" not in result[0]["html"]


def test_iframe_stripped():
    result = render_markdown_blocks('<iframe src="evil.com"></iframe>')
    assert "<iframe" not in result[0]["html"]


def test_data_uri_blocked():
    result = render_markdown_blocks(
        '<a href="data:text/html,<script>alert(1)</script>">x</a>'
    )
    # With html: False, markdown-it escapes the entire tag — no live <a> link.
    # The escaped text still contains "href=" as literal text, but there's
    # no functional <a> element in the DOM.
    assert "<a " not in result[0]["html"]


# ── Fenced code blocks ──────────────────────────────────────────────────


def test_fenced_code_block_rendered():
    """A fenced code block renders as a single <pre><code> block."""
    source = "```python\nx = 1\n```"
    result = render_markdown_blocks(source)
    assert len(result) == 1, "Fenced code block should be one block"
    html = result[0]["html"]
    assert "<pre>" in html
    assert "<code" in html
    assert "x = 1" in html
    # Backticks should not appear in the HTML
    assert "```" not in html


def test_fenced_code_block_preserves_raw():
    """Raw text includes the fence markers."""
    source = "```python\nx = 1\n```"
    result = render_markdown_blocks(source)
    assert "```python" in result[0]["raw"]
    assert "x = 1" in result[0]["raw"]
    assert result[0]["raw"].endswith("```")


def test_fenced_code_block_no_language():
    source = "```\nhello\n```"
    result = render_markdown_blocks(source)
    assert len(result) == 1
    assert "<pre>" in result[0]["html"]
    assert "<code>" in result[0]["html"]
    assert "```" not in result[0]["html"]


def test_fenced_code_block_multi_line():
    """Multiple content lines in a fenced block are one block."""
    source = "before\n\n```\nline1\nline2\nline3\n```\n\nafter"
    result = render_markdown_blocks(source)
    # "before" paragraph, fenced block, "after" paragraph
    assert len(result) == 3
    assert "before" in result[0]["html"]
    assert "after" in result[2]["html"]
    code_html = result[1]["html"]
    assert "<pre>" in code_html
    assert "line1" in code_html
    assert "line2" in code_html
    assert "line3" in code_html


def test_fenced_code_fence_lines_no_backticks():
    source = "```python\nx = 1\n```"
    result = render_markdown_blocks(source)
    assert "```" not in result[0]["html"]


# ── Mermaid blocks ───────────────────────────────────────────────────────


def test_mermaid_block_rendered_as_container():
    """A mermaid fenced block emits a single mermaid container div."""
    source = "```mermaid\ngraph TD\n    A-->B\n```"
    result = render_markdown_blocks(source)
    assert len(result) == 1
    html = result[0]["html"]
    assert 'class="mermaid"' in html
    assert "graph TD" in html
    assert "```" not in html


def test_mermaid_block_preserves_raw():
    source = "```mermaid\ngraph TD\n```"
    result = render_markdown_blocks(source)
    assert "```mermaid" in result[0]["raw"]
    assert "graph TD" in result[0]["raw"]


def test_mermaid_content_escaped():
    source = "```mermaid\nA-->B\n```"
    result = render_markdown_blocks(source)
    html = result[0]["html"]
    assert "A--&gt;B" in html or "A-->B" in html


# ── Content-derived block ids (#465) ─────────────────────────────────────


def _by_raw(source):
    return {b["raw"]: b["block_id"] for b in render_markdown_blocks(source)}


def test_every_block_has_a_block_id():
    blocks = render_markdown_blocks("# Title\n\nBody paragraph.\n\n- a\n- b\n")
    assert len(blocks) == 3
    assert all(b["block_id"] for b in blocks)


def test_block_id_format_is_hash_and_occurrence():
    blocks = render_markdown_blocks("only paragraph")
    assert re.fullmatch(r"[0-9a-f]{16}-\d+", blocks[0]["block_id"])


def test_block_ids_unique_within_a_document():
    source = "para\n\npara\n\npara\n"
    ids = [b["block_id"] for b in render_markdown_blocks(source)]
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_duplicate_blocks_share_hash_and_differ_by_occurrence():
    ids = [b["block_id"] for b in render_markdown_blocks("dup\n\ndup\n")]
    assert ids[0].rsplit("-", 1)[0] == ids[1].rsplit("-", 1)[0]
    assert ids[0].endswith("-1")
    assert ids[1].endswith("-2")


def test_block_id_stable_across_unrelated_edit_above():
    """Rewriting a block above must not change the target block's id."""
    before = "intro\n\n## Target\n\ntarget body\n"
    after = "intro, reworded\nand now two lines\n\n## Target\n\ntarget body\n"
    assert _by_raw(before)["target body"] == _by_raw(after)["target body"]
    assert _by_raw(before)["## Target"] == _by_raw(after)["## Target"]


def test_block_id_stable_when_block_moves_down():
    """Same content later in the file keeps the same id."""
    before = "target body\n"
    after = "a new opening paragraph\n\ntarget body\n"
    assert _by_raw(before)["target body"] == _by_raw(after)["target body"]


def test_block_id_changes_when_content_changes():
    assert _by_raw("original text\n")["original text"] != (
        _by_raw("rewritten text\n")["rewritten text"]
    )


def test_block_id_ignores_trailing_whitespace():
    """Trailing whitespace is invisible in the render, so it must not re-id."""
    clean = render_markdown_blocks("some text\nsecond line")[0]["block_id"]
    trailed = render_markdown_blocks("some text   \nsecond line\t")[0]["block_id"]
    assert clean == trailed


def test_block_id_of_first_block_unaffected_by_a_later_duplicate():
    """Occurrence numbering is unconditional, so adding a copy elsewhere in the
    file cannot renumber (and thus detach) the block that was already there."""
    before = _by_raw("dup\n\nother\n")["dup"]
    after = [b["block_id"] for b in render_markdown_blocks("dup\n\nother\n\ndup\n")]
    assert before == after[0]


def test_block_id_present_for_empty_source():
    blocks = render_markdown_blocks("")
    assert blocks[0]["block_id"]


def test_block_id_collapses_blank_line_runs():
    """Whitespace-only churn inside a block must not detach its comments."""
    one = "```text\nalpha\n\nbeta\n```"
    two = "```text\nalpha\n\n\n\nbeta\n```"
    assert (
        render_markdown_blocks(one)[0]["block_id"]
        == render_markdown_blocks(two)[0]["block_id"]
    )


def test_blank_line_collapse_collision_stays_disambiguated():
    """The known cost of collapsing blank lines: two fences whose only
    difference is a blank-line run hash alike.  The occurrence index still
    gives them distinct ids, so a comment cannot jump from one to the other."""
    source = "```text\na\n\nb\n```\n\n```text\na\n\n\nb\n```\n"
    ids = [b["block_id"] for b in render_markdown_blocks(source)]
    assert len(ids) == 2
    assert ids[0] != ids[1]
    assert ids[0].rsplit("-", 1)[0] == ids[1].rsplit("-", 1)[0]


def test_block_id_ignores_a_trailing_blank_line():
    """A blank line at the end of a block's raw range says nothing.

    A loose list carries one inside its own range, so without the trailing-blank
    trim an edit elsewhere that makes a list loose (or tight) would re-id it and
    detach every comment anchored to it.
    """
    from renderer import _normalize_raw

    assert _normalize_raw("- a\n\n- b\n") == _normalize_raw("- a\n\n- b")


def test_a_normalized_offset_survives_the_blank_run_it_points_past():
    """The id and the offset must be measured in the same space.

    `block_id` hashes the *normalized* text, so a block keeps its identity when
    a blank-line run inside it grows or shrinks — but its raw line numbering
    changes underneath.  An offset carried in raw lines therefore goes stale
    against an id that did not; one carried in normalized lines cannot.
    """
    from renderer import _normalize_raw, normalized_offset, raw_offset

    roomy = "```py\na = 1\n\n\n\nb = 2\n```"
    squeezed = "```py\na = 1\n\nb = 2\n```"
    assert _normalize_raw(roomy) == _normalize_raw(squeezed), "same id, by premise"

    # 'b = 2' is raw line 5 of the roomy fence and raw line 3 of the squeezed one.
    stored = normalized_offset(roomy, 5)
    assert raw_offset(squeezed, stored) == 3
    assert raw_offset(roomy, stored) == 5, "round-trips in its own frame too"


def test_a_raw_offset_inside_a_collapsed_run_maps_to_the_run():
    """Blank lines carry no text, so a comment on one maps to the run itself."""
    from renderer import normalized_offset, raw_offset

    roomy = "```py\na = 1\n\n\n\nb = 2\n```"
    # Raw lines 2, 3 and 4 are the blank run; normalized keeps one of them.
    assert normalized_offset(roomy, 3) == normalized_offset(roomy, 2)
    assert raw_offset(roomy, normalized_offset(roomy, 3)) == 2


def test_an_offset_naming_no_normalized_line_is_reported_not_clamped():
    """Out of range must be visible to the caller: a comment that cannot be
    placed inside its block has to keep the line blame gave it, and silently
    clamping to the nearest line would hide that from the decision."""
    from renderer import raw_offset

    assert raw_offset("alpha\nbeta", 5) is None
    assert raw_offset("alpha\nbeta", -1) is None
    assert raw_offset("alpha\nbeta", 1) == 1


# ── Disambiguating identical blocks by context (#467) ────────────────────


def _digest(block):
    return block["block_id"].rsplit("-", 1)[0]


def test_block_context_is_the_nearest_preceding_distinct_block():
    """What breaks the tie between identical blocks: what comes before them."""
    blocks = render_markdown_blocks("alpha\n\ndup\n\nbeta\n\ndup\n")
    alpha, first, beta, second = blocks
    assert first["block_context"] == _digest(alpha)
    assert second["block_context"] == _digest(beta)
    assert first["block_context"] != second["block_context"], (
        "the two copies must be told apart by something"
    )


def test_the_first_blocks_context_is_empty():
    """Nothing precedes it, and an absent context has to be a real value so it
    can be stored and compared like any other."""
    assert render_markdown_blocks("only para\n")[0]["block_context"] == ""


def test_context_ignores_an_identical_neighbour():
    """A run of copies would otherwise take its context from itself.

    Then inserting one more copy above the run would hand the *first* copy's
    context to the newcomer, rebinding its comments — the very failure being
    removed.  Skipping identical neighbours leaves an adjacent run ambiguous
    instead, which is no worse than the occurrence number it falls back to.
    """
    blocks = render_markdown_blocks("alpha\n\ndup\n\ndup\n")
    assert blocks[1]["block_context"] == blocks[2]["block_context"] == _digest(
        blocks[0]
    )


def _contexts_of(source, raw):
    return [
        b["block_context"] for b in render_markdown_blocks(source) if b["raw"] == raw
    ]


def test_a_copy_inserted_above_leaves_the_others_contexts_alone():
    """The property the whole scheme rests on.

    Asserted over the copies only.  A block whose text is unique in the document
    can well pick up a different context — 'alpha' gains a predecessor here —
    and it costs nothing: a unique block is matched on its text alone, so its
    context is never consulted.
    """
    before = _contexts_of("alpha\n\ndup\n\nbeta\n\ndup\n", "dup")
    after = _contexts_of("dup\n\nalpha\n\ndup\n\nbeta\n\ndup\n", "dup")
    assert len(before) == 2 and len(after) == 3
    assert after[1:] == before, "the copies that were already there must not move"


def test_deleting_a_copy_leaves_the_survivors_context_alone():
    before = render_markdown_blocks("alpha\n\ndup\n\nbeta\n\ndup\n")
    after = render_markdown_blocks("alpha\n\nbeta\n\ndup\n")
    assert after[-1]["block_context"] == before[-1]["block_context"]


def test_context_does_not_change_a_block_id():
    """`block_id` stays exactly what #465 specified.

    Folding the context into the id would make every id sensitive to edits
    elsewhere in the file, and would renumber a block the moment a copy of it
    appeared — the two things the unconditional occurrence suffix exists to
    prevent.  The context is a separate, additive anchor.
    """
    lonely = render_markdown_blocks("alpha\n\ntarget\n")[1]["block_id"]
    moved = render_markdown_blocks("rewritten opening\n\ntarget\n")[1]["block_id"]
    assert lonely == moved
    assert re.fullmatch(r"[0-9a-f]{16}-\d+", lonely)
