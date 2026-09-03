"""Render markdown source to HTML with per-block anchors.

Uses markdown-it-py to parse the whole document as block tokens.
Each block token carries ``token.map = [startLine, endLine]`` (0-based,
end-exclusive) giving its source line range.  Blocks are rendered to HTML
and returned with their source line ranges for comment anchoring.
"""

import hashlib
import html as html_mod
import posixpath
import re
from urllib.parse import quote, unquote

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
    # A cell can also carry a `class`, which this table cannot express: it is
    # synthesized from `style` by `_CELL_ALIGN_RE` below, never copied from the
    # input.  Read both before answering "what can appear on a cell?".
    "td": {"align"},
    "th": {"align"},
}

_TAG_RE = re.compile(r"<(/?)(\w+)(\s[^>]*)?>", re.DOTALL)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|\S+)')

# Column alignment is the one thing a markdown table can say that only `style`
# can carry: markdown-it emits `style="text-align:…"` on each cell, and `style`
# is whitelisted on no tag, so the alignment was dropped with it.  The value is
# matched whole against three literals and translated into a class the
# stylesheet knows.  Whitelisting `style` with a value pattern instead would put
# a parser inside the one function whose job is not trusting its input.
_CELL_ALIGN_RE = re.compile(
    r"\s*text-align\s*:\s*(left|center|right)\s*;?\s*\Z", re.IGNORECASE
)


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
            attr_val = am.group(2) if am.group(2) is not None else (am.group(3) or "")
            if tag in ("td", "th") and attr_name == "style":
                side = _CELL_ALIGN_RE.match(attr_val)
                if side is not None:
                    kept_attrs.append(f'class="align-{side.group(1).lower()}"')
                continue
            if attr_name in allowed:
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
    copies shares one context.  The case for it is a *deletion* inside a
    two-copy run (#494 item 3), which is what
    ``test_deleting_the_copy_above_keeps_the_run_tails_comment_attached``
    pins.  In the document that test writes: the survivor still records the
    context the comment stored, and the copy elsewhere in the document records
    a different one, so the comment is placed on the survivor.  Take the
    context from the identical neighbour instead and the tail stores the digest
    of the copy above it, which no survivor records at all; the context match
    is empty, resolution falls through to the occurrence number the deletion
    has just retired, and the comment is reported detached although its
    paragraph is still in the file.

    Both of those are that document's outcomes, and the scope stated below is
    not enough to generalize them: each has counterexamples *inside* that scope
    (#499).  What decides them is which other copies the document holds — a
    copy elsewhere recording the same context as the run makes the context
    match non-unique again, so the ordinal decides instead of the context, and
    an ordinal that some surviving copy still answers to leaves the comment
    attached, to that copy, where here it is detached.

    That case, and not a general guarantee (#539 review).  Neither rule
    dominates the other.  Every comparison below is made on the shape the test
    writes — a document whose *only* run of two or more copies of the text is
    the one being edited, isolated copies elsewhere allowed.  A second run of
    that text breaks each *rule* in turn and in both directions, so none of
    this is an ordering; it is why the choice rests on the case with a test
    behind it rather than on a count of hypotheticals.  (Each rule, not each
    comparison below: the head-of-run insertion is broken by nothing, second
    run or not.)

    Within that shape, the argument above runs backwards on a longer run:
    delete the copy above the tail of a *three*-copy run and the shared context
    matches both survivors, so the match is not unique and the stored ordinal is
    all that is left — it detaches the comment, or, where an isolated copy later
    in the document keeps that ordinal alive, silently moves it onto that
    unrelated copy, while the per-neighbour context would have named the
    survivor exactly.  Insertion at the head of a run is decided by neither *for
    a comment on that run* (three independent enumerations, no case) — the
    *head* copy's comment lands on the newcomer either way, a comment further
    down the run lands on the copy above it either way.  An insertion that
    *splits* the two-copy run is the case that runs against the skip: wherever
    the two rules disagree there, the per-neighbour context is the one that is
    right.  Split a longer run and even that reverses — split immediately above
    the tail of a three-copy run and the skip resolves the comment correctly
    while the per-neighbour context misses it by two paragraphs.
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


# A scheme-qualified URI: `https:`, `mailto:`, `file:` (RFC 3986 §3.1).
_URI_SCHEME_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*:")


def _leaves_root(resolved: str) -> bool:
    """Whether a normalized target has climbed out of the served root.

    ``"."`` counts.  ``normpath`` spells the parent of a top-level file that
    way — ``kb/index.md`` linking to ``..`` normalizes to ``"."``, not to
    ``".."`` — so a one-level climb looks nothing like a two-level one.
    """
    return resolved in (".", "..") or resolved.startswith("../")


def _resolve_doc_link(href: str, doc_path: str) -> str | None:
    """Rewrite a relative link in *doc_path* to a URL the server answers.

    Returns ``None`` for a link that already resolves and must be left exactly
    as the author wrote it.

    A relative href resolves against the *browser's* location, which for every
    document this app shows is ``/view?path=…``.  So ``[a](a.md)`` in
    ``kb/wiki/index.md`` asks the browser for ``/a.md`` — a route the server
    does not serve, and a directory the file never meant.  The file's own
    location is the only thing that says what ``a.md`` refers to, and the
    renderer learns it only because the caller passes it in.

    Left alone: an absolute or protocol-relative URL, anything carrying a
    scheme, a root-relative path, and a bare ``#fragment``, which addresses the
    page already on screen.  A target that climbs out of the source root is
    also left alone: ``_resolve_file`` refuses it with a 403, and pointing it
    at ``/view`` would dress a link the reader cannot follow up as a working
    one.
    """
    if not href or href.startswith(("#", "/")) or _URI_SCHEME_RE.match(href):
        return None

    target, sep, fragment = href.partition("#")
    if not target:
        return None

    base = posixpath.dirname(doc_path)
    resolved = posixpath.normpath(posixpath.join(base, target))
    # Checked decoded as well as raw.  markdown-it preserves a percent-escape
    # it is given, so `%2e%2e/` reaches here still spelled that way and
    # `normpath` has no `..` segment to fold.  `_resolve_file` would refuse the
    # result with a 403 either way, but the point of the guard is to leave a
    # link the reader cannot follow looking like one, rather than dressing it
    # up as a working `/view` URL.
    if _leaves_root(resolved) or _leaves_root(
        posixpath.normpath(posixpath.join(base, unquote(target)))
    ):
        return None

    # markdown-it has already percent-encoded both halves, and escapes a bare
    # `%` as `%25` while doing so.  `safe="/%"` therefore adds only what a
    # *query value* cannot additionally hold raw — `&` and `?` above all, which
    # would otherwise end the `path` parameter early — without escaping the
    # escapes a second time.  The fragment is encoded on the same terms rather
    # than passed through: this function builds a URL, so what can appear in it
    # is its own answer to give, not markdown-it's to be trusted for.
    return (
        f"/view?path={quote(resolved, safe='/%')}"
        f"{sep}{quote(fragment, safe='/%')}"
    )


def _new_parser(doc_path: str | None = None) -> MarkdownIt:
    """Create a MarkdownIt parser with our standard settings.

    ``front_matter_plugin`` consumes a leading ``---`` metadata block as a
    single token (#452).  Without it CommonMark reads the closing ``---`` as a
    setext heading underline, so ``---\\nmarp: true\\n---`` renders as an
    ``<hr>`` followed by a bogus ``<h2>marp: true</h2>``.

    With *doc_path*, links relative to that file are rewritten to ``/view``
    URLs.  The rewrite is a renderer rule rather than a pass over the finished
    HTML because a token's ``href`` is a parsed value, whereas the same string
    in rendered markup has to be found again with a regex and un-escaped before
    it can be read.  ``html: False`` means every ``<a>`` in the output came
    from a markdown link, so the rule sees all of them.
    """
    md = (
        MarkdownIt("commonmark", {"html": False})
        .enable("table")
        .use(front_matter_plugin)
    )
    if doc_path is not None:
        default = md.renderer.renderToken

        def _link_open(tokens, idx, options, env):
            resolved = _resolve_doc_link(tokens[idx].attrGet("href") or "", doc_path)
            if resolved is not None:
                tokens[idx].attrSet("href", resolved)
            return default(tokens, idx, options, env)

        md.renderer.rules["link_open"] = _link_open
    return md


def render_markdown_blocks(source: str, doc_path: str | None = None) -> list[dict]:
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

    *doc_path* is *source*'s path relative to the served root, and only the
    ``html`` depends on it: links relative to that file are rewritten to point
    at ``/view``.  Callers that want block identity or line ranges — comment
    anchoring, the parity fixture — pass nothing and get the html they always
    got.  ``block_id`` hashes the block's ``raw`` source, so it does not move
    either way.
    """
    lines = source.split("\n")
    md = _new_parser(doc_path)
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
