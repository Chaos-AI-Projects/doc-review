"""Tests for the markdown renderer."""

from renderer import render_markdown_lines


def test_basic_lines():
    source = "line one\nline two\nline three"
    result = render_markdown_lines(source)
    assert len(result) == 3
    assert result[0]["number"] == 1
    assert result[0]["raw"] == "line one"
    assert result[1]["number"] == 2
    assert result[2]["number"] == 3


def test_empty_source():
    result = render_markdown_lines("")
    assert len(result) == 1
    assert result[0]["number"] == 1
    assert result[0]["raw"] == ""


def test_markdown_bold():
    result = render_markdown_lines("**bold text**")
    assert "<strong>bold text</strong>" in result[0]["html"]


def test_markdown_inline_code():
    result = render_markdown_lines("`code`")
    assert "<code>code</code>" in result[0]["html"]


def test_heading_rendered():
    result = render_markdown_lines("# Heading")
    # md.markdown renders # Heading as <h1>Heading</h1>
    # We don't strip h1 tags, only p tags
    assert "Heading" in result[0]["html"]


def test_preserves_raw():
    source = "**bold** and `code`"
    result = render_markdown_lines(source)
    assert result[0]["raw"] == source


def test_script_tag_stripped():
    result = render_markdown_lines('<script>alert("xss")</script>')
    assert "<script>" not in result[0]["html"]
    assert "</script>" not in result[0]["html"]


def test_img_onerror_stripped():
    result = render_markdown_lines('<img src=x onerror=alert(1)>')
    # img is not in the allowed tags, so the entire tag is removed
    assert "onerror" not in result[0]["html"]


def test_iframe_stripped():
    result = render_markdown_lines('<iframe src="evil.com"></iframe>')
    assert "<iframe" not in result[0]["html"]


def test_attribute_injection_via_quote_mismatch():
    """Single-quoted attr with embedded double-quote must not inject attributes.

    Without the fix, the output would be:
        <a href="x" onmouseover="alert(1)">click</a>
    which has onmouseover as a real attribute.

    With the fix, the output is:
        <a href="x&quot; onmouseover=&quot;alert(1)">click</a>
    where &quot; keeps onmouseover trapped inside the href value.
    """
    result = render_markdown_lines("""<a href='x" onmouseover="alert(1)'>click</a>""")
    html = result[0]["html"]
    # The double-quotes must be entity-encoded so the injected onmouseover
    # stays inside the href value and is not a separate attribute.
    assert '&quot;' in html
    # The critical check: no literal unescaped double-quote followed by
    # onmouseover as a separate attribute. The safe form has &quot; before it.
    # If the output had `" onmouseover=` with a real quote, it would be an
    # injection. With &quot; the browser treats it as part of href's value.
    assert 'href="x&quot;' in html


def test_data_uri_blocked():
    result = render_markdown_lines('<a href="data:text/html,<script>alert(1)</script>">x</a>')
    # The href should be dropped since data: is blocked
    assert "data:" not in result[0]["html"]


def test_fenced_code_block_rendered():
    """A multi-line fenced code block must render as <pre>/<code>, not literal backticks,
    and each of the source lines must still get its own numbered entry."""
    source = "```python\nx = 1\n```"
    result = render_markdown_lines(source)
    assert len(result) == 3
    assert result[0]["number"] == 1
    assert result[1]["number"] == 2
    assert result[2]["number"] == 3
    # The opening fence, code line, and closing fence should NOT contain literal backticks
    for entry in result:
        assert "```" not in entry["html"]
    # The code line should be wrapped in <code> styling
    assert "<code>" in result[1]["html"]


def test_fenced_code_block_preserves_raw():
    """Raw text must be preserved unchanged for fenced code blocks."""
    source = "```python\nx = 1\n```"
    result = render_markdown_lines(source)
    assert result[0]["raw"] == "```python"
    assert result[1]["raw"] == "x = 1"
    assert result[2]["raw"] == "```"


def test_fenced_code_block_no_language():
    """Fenced blocks without a language hint should also be rendered as code."""
    source = "```\nhello\n```"
    result = render_markdown_lines(source)
    assert len(result) == 3
    assert "```" not in result[1]["html"]
    assert "<code>" in result[1]["html"]


def test_fenced_code_block_multi_line():
    """Multiple lines inside a fenced block should all be code-styled."""
    source = "before\n```\nline1\nline2\nline3\n```\nafter"
    result = render_markdown_lines(source)
    assert len(result) == 7
    # lines 3-5 (index 2-4) are inside the fence
    for i in [2, 3, 4]:
        assert "<code>" in result[i]["html"]
        assert "```" not in result[i]["html"]
    # Lines outside the fence should be normal
    assert "before" in result[0]["html"]
    assert "after" in result[6]["html"]


def test_fenced_code_fence_lines_no_backticks():
    """The opening and closing fence lines themselves should not show literal backticks."""
    source = "```python\nx = 1\n```"
    result = render_markdown_lines(source)
    assert "```" not in result[0]["html"]
    assert "```" not in result[2]["html"]


def test_mermaid_block_rendered_as_container():
    """A ```mermaid fenced block must emit a mermaid container div, not <code>."""
    source = "```mermaid\ngraph TD\n    A-->B\n```"
    result = render_markdown_lines(source)
    assert len(result) == 4
    # All lines keep their anchors
    for idx, entry in enumerate(result):
        assert entry["number"] == idx + 1
    # No literal backticks
    for entry in result:
        assert "```" not in entry["html"]
    # The opening line should reference mermaid
    assert "mermaid" in result[0]["html"]
    # Content lines should be inside a mermaid container (div with class)
    assert 'class="mermaid"' in result[1]["html"] or "mermaid" in result[1]["html"]


def test_mermaid_block_preserves_raw():
    """Raw text must be preserved unchanged for mermaid blocks."""
    source = "```mermaid\ngraph TD\n```"
    result = render_markdown_lines(source)
    assert result[0]["raw"] == "```mermaid"
    assert result[1]["raw"] == "graph TD"
    assert result[2]["raw"] == "```"


def test_mermaid_content_not_html_escaped_for_rendering():
    """Mermaid content should be placed in the container for client-side rendering,
    with arrows preserved (not over-escaped)."""
    source = "```mermaid\nA-->B\n```"
    result = render_markdown_lines(source)
    # The content line should contain the mermaid source for client-side rendering
    assert "A--&gt;B" in result[1]["html"] or "A-->B" in result[1]["html"]
