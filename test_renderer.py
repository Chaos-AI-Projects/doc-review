"""Tests for the markdown block renderer."""

from renderer import extract_toc, render_markdown_blocks


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
