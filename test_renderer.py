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
