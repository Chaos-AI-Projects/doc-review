"""Render markdown source to HTML with per-block anchors.

Uses markdown-it-py to parse the whole document as block tokens.
Each block token carries ``token.map = [startLine, endLine]`` (0-based,
end-exclusive) giving its source line range.  Blocks are rendered to HTML
and returned with their source line ranges for comment anchoring.
"""

import hashlib
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


def _normalized_lines(raw: str) -> tuple[list[str], list[int]]:
    """The normalized lines of *raw*, and where each one came from.

    The single place that decides what normalization does, so a block's id and
    any position *inside* that block are necessarily computed in the same
    coordinate space (#465 round 4).  ``sources[i]`` is the 0-based raw line
    that ``normalized[i]`` was taken from.

    Keeping the two derivations together is the point: when the id hashed
    normalized text while offsets counted raw lines, an edit that collapsed a
    blank-line run left the id valid and the offset stale, and a comment was
    placed on text it was never written about.
    """
    normalized: list[str] = []
    sources: list[int] = []
    for index, line in enumerate(raw.split("\n")):
        line = line.rstrip()
        if not line and (not normalized or not normalized[-1]):
            continue  # leading blank, or a second blank in a row
        normalized.append(line)
        sources.append(index)
    while normalized and not normalized[-1]:
        normalized.pop()
        sources.pop()
    return normalized, sources


def _normalize_raw(raw: str) -> str:
    """Canonical form of a block's source for identity purposes (#465).

    Strips trailing whitespace per line and collapses blank-line runs, so an
    edit that does not change what the block *says* does not change its id —
    and therefore does not detach the comments anchored to it.
    """
    return "\n".join(_normalized_lines(raw)[0])


def normalized_offset(raw: str, offset: int) -> int | None:
    """Convert a raw line offset into *raw* to a normalized one.

    Returns ``None`` only when the block normalizes to nothing at all, and so
    has no line to name.  A raw offset that normalization discarded — a blank
    inside a collapsed run, or padding past the last line of text — maps to the
    nearest kept line at or before it: blank lines carry no text, so there is
    nothing finer to preserve.
    """
    _, sources = _normalized_lines(raw)
    if not sources:
        return None
    placed = 0
    for index, source in enumerate(sources):
        if source > offset:
            break
        placed = index
    return placed


def raw_offset(raw: str, offset: int) -> int | None:
    """Convert a normalized line offset into *raw* back to a raw one.

    Returns ``None`` when *offset* names no line of this block, rather than
    clamping to the nearest one.  The caller has to be able to tell "this
    comment belongs on line N" from "this offset means nothing here" — the
    second is not a position, and guessing one would silently overrule better
    evidence about where the comment goes.
    """
    _, sources = _normalized_lines(raw)
    if not 0 <= offset < len(sources):
        return None
    return sources[offset]


def _assign_block_ids(blocks: list[dict]) -> list[dict]:
    """Give each block a content-derived id, unique within the document.

    ``block_id`` is ``sha256(normalized raw)[:16]`` plus a 1-based occurrence
    suffix.  The suffix is unconditional rather than only on duplicates: if the
    first copy of a repeated block were bare and only later copies numbered,
    pasting a second copy anywhere in the file would renumber the first one and
    detach its comments.

    Numbering is positional, so it says nothing durable about *which* copy of a
    repeated block it names: deleting the first copy, or inserting a fresh one
    above the others, renumbers everything after it and swaps those comments
    over.  Marp ``---`` slide separators are the real instance.  ``block_context``
    is what tells the copies apart instead (#467): the digest of the nearest
    preceding block whose text differs, or ``""`` at the start of the document.

    Context is carried *beside* the id rather than folded into it, and the id is
    unchanged from #465.  An id that hashed its surroundings would move whenever
    those surroundings were edited, so rewording one paragraph would detach the
    comments on the next; and making the suffix conditional on a block having
    copies would re-identify it the moment a copy appeared, which is what the
    unconditional suffix above exists to prevent.  Both are commoner edits than
    reordering identical blocks, so context stays a tiebreak between blocks that
    are already identical and is invisible to every block whose text is unique.

    Identical *neighbours* are skipped when looking back, so a run of adjacent
    copies shares one context.  Taking the context from an identical neighbour
    would look more discriminating and be worse: inserting one more copy at the
    head of the run would hand the first copy's context to the newcomer and
    rebind its comments.  An adjacent run stays ambiguous and falls back to the
    occurrence number, which is where it already was.
    """
    seen: dict[str, int] = {}
    digests = [
        hashlib.sha256(_normalize_raw(block["raw"]).encode("utf-8")).hexdigest()[:16]
        for block in blocks
    ]
    for index, block in enumerate(blocks):
        digest = digests[index]
        occurrence = seen[digest] = seen.get(digest, 0) + 1
        block["block_id"] = f"{digest}-{occurrence}"
        block["block_context"] = next(
            (d for d in reversed(digests[:index]) if d != digest), ""
        )
    return blocks


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
    ``{"start_line": int, "end_line": int, "type": str, "raw": str,
    "html": str, "block_id": str}``

    - ``start_line`` and ``end_line`` are 1-based inclusive.
    - ``block_id`` is a content-derived identity (#465) that survives edits
      elsewhere in the file, so a comment can be re-anchored to the block it
      was written about rather than to a line number.
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
        return _assign_block_ids(
            [{"start_line": 1, "end_line": 1, "raw": "", "html": ""}]
        )

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

    return _assign_block_ids(
        blocks or [{"start_line": 1, "end_line": 1, "raw": "", "html": ""}]
    )


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
