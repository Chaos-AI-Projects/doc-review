"""Render markdown source files to HTML with per-line anchors."""

import html as html_mod
import re

import markdown as md

# Regex to detect a fenced code block opening/closing line.
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(\w*)\s*$")

# Allowed HTML tags for sanitized markdown output.
_ALLOWED_TAGS = frozenset({
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl",
    "dt", "em", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "ins", "kbd",
    "li", "ol", "p", "pre", "q", "s", "samp", "small", "span", "strong",
    "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "tt", "u",
    "ul", "var",
})

# Allowed attributes per tag.
_ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "abbr": {"title"},
    "div": {"class"},
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


def _detect_fence_regions(lines: list[str]) -> list[tuple[int, int, str]]:
    """Return (start, end, lang) tuples for fenced code blocks in *lines*.

    *start* and *end* are 0-based indices (inclusive). *lang* is the language
    hint from the opening fence (empty string if none).
    """
    regions: list[tuple[int, int, str]] = []
    fence_char = None
    fence_len = 0
    start = -1
    lang = ""
    for idx, line in enumerate(lines):
        m = _FENCE_RE.match(line)
        if m and fence_char is None:
            # Opening fence.
            fence_char = m.group(1)[0]
            fence_len = len(m.group(1))
            lang = m.group(2)
            start = idx
        elif fence_char is not None and m:
            closing_char = m.group(1)[0]
            closing_len = len(m.group(1))
            if closing_char == fence_char and closing_len >= fence_len and not m.group(2):
                # Closing fence.
                regions.append((start, idx, lang))
                fence_char = None
                fence_len = 0
                start = -1
                lang = ""
    return regions


def render_markdown_lines(source: str) -> list[dict]:
    """Split *source* into lines and return per-line data.

    Each entry: {"number": int, "raw": str, "html": str}
    - ``number`` is 1-based.
    - ``raw`` is the original line text.
    - ``html`` is the line rendered as sanitized inline HTML. Dangerous tags
      (script, iframe, etc.) and event-handler attributes are stripped.

    Multi-line fenced code blocks (``` or ~~~) are detected across lines and
    rendered with ``<code>`` styling while preserving the per-line anchor model.
    """
    lines = source.split("\n")
    fence_regions = _detect_fence_regions(lines)

    # Build a lookup: line index -> (region_role, lang)
    # role: "open", "code", "close"
    fence_map: dict[int, tuple[str, str]] = {}
    for start, end, lang in fence_regions:
        fence_map[start] = ("open", lang)
        for ci in range(start + 1, end):
            fence_map[ci] = ("code", lang)
        fence_map[end] = ("close", lang)

    result = []
    for i, line in enumerate(lines):
        raw = line
        info = fence_map.get(i)
        if info is not None:
            role, lang = info
            is_mermaid = lang == "mermaid"
            if role == "open":
                # Opening fence: show language label if present, no backticks.
                if is_mermaid:
                    html = '<code class="language-mermaid">mermaid</code>'
                elif lang:
                    html = f'<code class="language-{html_mod.escape(lang)}">{html_mod.escape(lang)}</code>'
                else:
                    html = ""
            elif role == "close":
                # Closing fence: render empty.
                html = ""
            elif is_mermaid:
                # Mermaid content: wrap in a div.mermaid for client-side rendering.
                html = f'<div class="mermaid">{html_mod.escape(line)}</div>'
            else:
                # Code line: escape and wrap in <code>.
                html = f"<code>{html_mod.escape(line)}</code>"
        else:
            # Normal line: render inline markdown.
            html = md.markdown(line, extensions=["fenced_code", "tables"])
            # md.markdown wraps in <p>...</p>; unwrap for inline display.
            if html.startswith("<p>") and html.endswith("</p>"):
                html = html[3:-4]
            html = _sanitize_html(html)
        result.append({"number": i + 1, "raw": raw, "html": html})
    return result
