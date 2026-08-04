"""Render markdown source to HTML with per-block anchors.

Uses markdown-it-py to parse the whole document as block tokens.
Each block token carries ``token.map = [startLine, endLine]`` (0-based,
end-exclusive) giving its source line range.  Blocks are rendered to HTML
and returned with their source line ranges for comment anchoring.
"""

import html as html_mod
import re

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin

# Allowed HTML tags for sanitized output (defense-in-depth).
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
    "code": {"class"},
    "div": {"class"},
    "pre": {"class"},
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


def _new_parser() -> MarkdownIt:
    """Create a MarkdownIt parser with our standard settings.

    ``front_matter_plugin`` consumes a leading ``---`` metadata block as a
    single token (#452).  Without it CommonMark reads the closing ``---`` as a
    setext heading underline, so ``---\\nmarp: true\\n---`` renders as an
    ``<hr>`` followed by a bogus ``<h2>marp: true</h2>``.
    """
    return (
        MarkdownIt("commonmark", {"html": False})
        .enable("table")
        .use(front_matter_plugin)
    )


def render_markdown_blocks(source: str) -> list[dict]:
    """Parse *source* as markdown and return per-block data.

    Each entry:
    ``{"start_line": int, "end_line": int, "type": str, "raw": str, "html": str}``

    - ``start_line`` and ``end_line`` are 1-based inclusive.
    - ``type`` is the block's markdown-it token type (``"hr"``, ``"paragraph"``,
      ``"front_matter"``, …), so callers can make structural decisions without
      pattern-matching rendered HTML (#452 slide grouping).
    - ``raw`` is the original source text for the block's lines.
    - ``html`` is the block rendered as sanitized HTML.

    Mermaid fenced blocks are rendered as ``<div class="mermaid">`` containers.
    """
    lines = source.split("\n")
    md = _new_parser()
    tokens = md.parse(source)

    if not tokens:
        # Empty source — return a single empty block.
        return [{"start_line": 1, "end_line": 1, "raw": "", "html": ""}]

    # Walk the flat token list to identify top-level blocks.
    blocks: list[dict] = []
    i = 0
    depth = 0
    block_start_idx: int | None = None

    while i < len(tokens):
        t = tokens[i]
        if depth == 0:
            if t.nesting == 0 and t.map is not None:
                # Self-closing top-level block (fence, hr, code_block).
                blocks.append(
                    _render_block(tokens[i : i + 1], t.map, lines, md)
                )
            elif t.nesting == 1 and t.map is not None:
                # Opening of a top-level block.
                block_start_idx = i
                depth = 1
            # nesting == -1 at depth 0 should not happen.
        else:
            depth += t.nesting
            if depth == 0:
                assert block_start_idx is not None
                bmap = tokens[block_start_idx].map
                blocks.append(
                    _render_block(
                        tokens[block_start_idx : i + 1], bmap, lines, md
                    )
                )
                block_start_idx = None
        i += 1

    return blocks if blocks else [{"start_line": 1, "end_line": 1, "raw": "", "html": ""}]


def _render_block(
    block_tokens: list, line_map: list[int], source_lines: list[str], md: MarkdownIt
) -> dict:
    start_0, end_0 = line_map  # 0-based, end-exclusive
    start_1 = start_0 + 1  # 1-based inclusive
    end_1 = end_0  # end_0 exclusive → end_0 is 1-based inclusive
    raw = "\n".join(source_lines[start_0:end_0])

    first = block_tokens[0]

    # Special-case mermaid fenced blocks.
    if (
        len(block_tokens) == 1
        and first.type == "fence"
        and first.info.strip() == "mermaid"
    ):
        content = first.content.rstrip("\n")
        rendered = f'<div class="mermaid">{html_mod.escape(content)}</div>'
    elif first.type == "front_matter":
        # The plugin renders front matter to nothing.  Show it instead: it is
        # document metadata a reviewer may want to read and comment on, and a
        # block that renders to nothing would look like lost content.
        content = first.content.strip("\n")
        rendered = f'<pre class="front-matter">{html_mod.escape(content)}</pre>'
    else:
        rendered = md.renderer.render(block_tokens, md.options, {})
        rendered = _sanitize_html(rendered)

    return {
        "start_line": start_1,
        "end_line": end_1,
        "type": first.type.removesuffix("_open"),
        "raw": raw,
        "html": rendered.strip(),
    }


def extract_toc(source: str) -> list[dict]:
    """Extract a table of contents from heading tokens.

    Returns a list of ``{"level": int, "text": str, "start_line": int}``.
    """
    md = _new_parser()
    tokens = md.parse(source)
    toc: list[dict] = []
    for i, t in enumerate(tokens):
        if t.type == "heading_open" and t.map is not None:
            level = int(t.tag[1])  # "h1" → 1, "h2" → 2, etc.
            # The next token is the inline content with the heading text.
            if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
                text = tokens[i + 1].content
                toc.append(
                    {"level": level, "text": text, "start_line": t.map[0] + 1}
                )
    return toc
