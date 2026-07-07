"""Render markdown source files to HTML with per-line anchors."""

import re

import markdown as md

# Allowed HTML tags for sanitized markdown output.
_ALLOWED_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "dl", "dt",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "ins", "kbd",
    "li", "ol", "p", "pre", "q", "s", "samp", "small", "span", "strong",
    "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "tt", "u",
    "ul", "var",
})

# Allowed attributes per tag.
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "abbr": {"title"},
    "td": {"align"},
    "th": {"align"},
}

_TAG_RE = re.compile(r"<(/?)(\w+)(\s[^>]*)?>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|\S+)')


def _sanitize_html(html: str) -> str:
    """Remove HTML tags and attributes not in the whitelist."""
    def _replace_tag(m: re.Match) -> str:
        closing = m.group(1)
        tag = m.group(2).lower()
        attrs_str = m.group(3) or ""

        if tag not in _ALLOWED_TAGS:
            return ""

        if closing:
            return f"</{tag}>"

        # Filter attributes.
        allowed = _ALLOWED_ATTRS.get(tag, set())
        kept_attrs = []
        for am in _ATTR_RE.finditer(attrs_str):
            attr_name = am.group(1).lower()
            if attr_name in allowed:
                attr_val = am.group(2) if am.group(2) is not None else (am.group(3) or "")
                # Block dangerous URI schemes in href
                if attr_name == "href" and re.match(
                    r"\s*(javascript|data|vbscript):", attr_val, re.IGNORECASE
                ):
                    continue
                # Escape quotes to prevent attribute injection
                attr_val = attr_val.replace("&", "&amp;").replace('"', "&quot;")
                kept_attrs.append(f'{attr_name}="{attr_val}"')

        if kept_attrs:
            return f"<{tag} {' '.join(kept_attrs)}>"
        return f"<{tag}>"

    return _TAG_RE.sub(_replace_tag, html)


def render_markdown_lines(source: str) -> list[dict]:
    """Split *source* into lines and return per-line data.

    Each entry: {"number": int, "raw": str, "html": str}
    - ``number`` is 1-based.
    - ``raw`` is the original line text.
    - ``html`` is the line rendered as sanitized inline HTML. Dangerous tags
      (script, iframe, etc.) and event-handler attributes are stripped.
    """
    lines = source.split("\n")
    result = []
    for i, line in enumerate(lines, 1):
        # Render inline markdown for each line; strip wrapping <p> tag.
        html = md.markdown(line, extensions=["fenced_code", "tables"])
        # md.markdown wraps in <p>...</p>; unwrap for inline display.
        if html.startswith("<p>") and html.endswith("</p>"):
            html = html[3:-4]
        html = _sanitize_html(html)
        result.append({"number": i, "raw": line, "html": html})
    return result
