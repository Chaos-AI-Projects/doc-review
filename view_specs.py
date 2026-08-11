"""Render-spec builders for the source table, TOC and file header (#451).

ONE source of truth for the row/TOC markup shape.  The same functions are
consumed by:

- the server-side Jinja render (``templates/view.html`` via ``/view``), and
- the client-side soft swap, which loads this file verbatim into the warm
  Pyodide runtime (``GET /py/view_specs.py``).

Keeping both paths on the same code makes it structurally impossible for a
swapped-in file to render differently from a fresh ``/view`` — the drift these
used to be two copies of lived in ``static/nav_logic.js``.

Two constraints shape this module:

- **No imports.**  Pyodide loads it as a bare file next to ``renderer.py``,
  with nothing but micropip'd ``markdown-it-py`` in the environment.
- **camelCase spec keys.**  These dicts cross the WASM bridge and are consumed
  verbatim by DOM code in ``static/app.js``; they are a JS-facing contract, not
  Python-internal data.

The ``id="L{start_line}"`` anchor built here is what comments are attached to.
It must not change (cf. the 2026-07-22 comment-loss incident).
"""


# Themes presentation mode ships CSS for (#452).  An unknown front-matter
# value falls back to "default" rather than reaching a class attribute.
PRESENTATION_THEMES = ("default", "gaia", "uncover")

# Per-slide layouts presentation mode ships CSS for (#462).  Same rule as the
# themes above: the name lands in a class attribute, so only ours may reach it.
PRESENTATION_LAYOUTS = ("default", "title", "centered", "quote")

# The two Marp spellings of the layout directive.  They differ only in scope:
# the leading underscore marks a "spot" directive, which applies to the slide
# it sits on and no other; the bare form is global and carries forward.
_SPOT_LAYOUT = "_class"
_GLOBAL_LAYOUT = "class"


def row_class(comment_count):
    """CSS classes for a source row, flagged when it carries comments."""
    return "source-line" + (" has-comments" if comment_count else "")


def line_label(block):
    """Gutter label: ``"4"`` for a single line, ``"4-7"`` for a range."""
    start, end = block["start_line"], block["end_line"]
    return f"{start}-{end}" if end != start else str(start)


def _comment_count(comments_by_block, start_line):
    """Comments anchored to *start_line*.

    The server passes a dict keyed by int; the same dict arrives from JSON on
    the client keyed by str.  Both must resolve or a soft swap would silently
    drop the comment markers.
    """
    if not comments_by_block:
        return 0
    entries = comments_by_block.get(start_line)
    if entries is None:
        entries = comments_by_block.get(str(start_line))
    return len(entries or [])


def source_row_specs(blocks, comments_by_block=None):
    """Describe the source table rows for a rendered document."""
    specs = []
    for block in blocks or []:
        count = _comment_count(comments_by_block, block["start_line"])
        specs.append(
            {
                "id": f"L{block['start_line']}",
                "rowClass": row_class(count),
                "startLine": block["start_line"],
                "endLine": block["end_line"],
                "label": line_label(block),
                "html": block["html"],
                "commentCount": count,
            }
        )
    return specs


def toc_item_specs(toc):
    """Describe the table-of-contents entries in the file navigator."""
    return [
        {
            "className": f"toc-item toc-level-{entry['level']}",
            "href": f"#L{entry['start_line']}",
            "text": entry["text"],
        }
        for entry in toc or []
    ]


def _append_slide(slides, rows, layout):
    """Append a slide unless it would be blank (leading/adjacent breaks)."""
    if rows:
        slides.append(
            {
                "index": len(slides),
                "number": len(slides) + 1,
                "layout": layout,
                "rows": rows,
            }
        )


def _is_slide_break(block):
    """Is *block* a Marp slide break?

    Marp splits on ``---``.  ``***`` and ``___`` are thematic breaks too, but
    they are not slide breaks — they stay visible on the slide rather than
    silently cutting the deck.  Checking ``type`` rather than the rendered HTML
    keeps this decision structural: a ``---`` inside a fence is content, and a
    ``---`` under a text line is a setext heading underline, and neither is
    typed ``hr``.
    """
    return block.get("type") == "hr" and set(block.get("raw", "").strip()) == {"-"}


def comment_directives(block):
    """Marp directives carried by *block*, or ``{}`` if it carries none.

    Read from ``raw``, never from ``html``.  The parser runs with
    ``html: False`` (``renderer.py``), so an HTML comment is not invisible
    here — it arrives as an ordinary ``paragraph`` whose html is the *escaped*
    comment text, which is why a real Marp deck used to render its own
    directives as visible body text on the slide.

    A block counts as a directive block only when it is a ``paragraph``, is
    entirely one HTML comment, every line inside it reads ``key: value``, and
    at least one key is a directive this module acts on.  An ordinary comment
    (``<!-- TODO: later -->``) is deliberately left alone: swallowing every
    comment would delete document content on the strength of a guess, and that
    block carries a comment anchor like any other.

    The ``type`` check is structural for the same reason ``_is_slide_break``
    checks it: a directive *shown as an example* is content.  Indented code
    keeps its indentation in ``raw`` but strips to a bare comment, so the text
    alone cannot tell a directive from a deck documenting one.
    """
    if block.get("type") != "paragraph":
        return {}
    raw = (block.get("raw") or "").strip()
    if not (raw.startswith("<!--") and raw.endswith("-->")):
        return {}
    directives = {}
    for line in raw[len("<!--") : -len("-->")].split("\n"):
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            return {}
        directives[key.strip().lower()] = value.strip()
    if _SPOT_LAYOUT not in directives and _GLOBAL_LAYOUT not in directives:
        return {}
    return directives


def _layout_name(name):
    """Whitelist a layout name on its way into a CSS class (cf. ``theme``)."""
    return name if name in PRESENTATION_LAYOUTS else "default"


def slide_specs(blocks, comments_by_block=None):
    """Group the review-mode rows into slides (#452).

    A slide is the run of blocks between two ``---`` breaks.  The rows are the
    *same* specs ``source_row_specs()`` builds for review mode — presentation
    mode is a grouping layer, not a second markup path — so every block keeps
    an identical line range, and therefore an identical ``id="L{start_line}"``
    comment anchor, in both modes.

    The breaks themselves are delimiters, and front matter and ``_class``
    directive blocks are metadata, so none of them becomes slide content.
    Dropping a block is safe; renumbering one is not, so nothing here touches
    a line range.
    """
    specs = source_row_specs(blocks, comments_by_block)
    slides = []
    rows = []
    spot = ""     # this slide only, cleared at every break
    global_ = ""  # carries forward until another `class:` replaces it
    for block, spec in zip(blocks or [], specs):
        if _is_slide_break(block):
            _append_slide(slides, rows, _layout_name(spot or global_))
            rows = []
            spot = ""
            continue
        if block.get("type") == "front_matter":
            continue
        directives = comment_directives(block)
        if directives:
            spot = directives.get(_SPOT_LAYOUT, spot)
            global_ = directives.get(_GLOBAL_LAYOUT, global_)
            continue
        rows.append(spec)
    _append_slide(slides, rows, _layout_name(spot or global_))
    return slides


def front_matter_directives(source):
    """Global directives from a leading ``---`` front-matter block.

    Mirrors the ``front_matter`` parser rule: the block opens only on line 1
    and runs to the next bare ``---``.  Values are returned as written; the
    caller decides what a directive means.
    """
    lines = (source or "").split("\n")
    if not lines or lines[0].strip() != "---":
        return {}
    directives = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return directives
        key, sep, value = line.partition(":")
        if sep and key.strip():
            directives[key.strip().lower()] = value.strip()
    return {}  # unterminated: the parser does not see front matter either


def presentation_specs(blocks, comments_by_block=None, source=""):
    """Everything the client needs to present a document as slides (#452).

    Honours the global ``marp`` / ``theme`` / ``paginate`` front-matter
    directives, plus the per-slide ``_class`` / ``class`` layout directives
    each slide carries on its own spec (#462).
    """
    directives = front_matter_directives(source)
    slides = slide_specs(blocks, comments_by_block)
    theme = directives.get("theme", "")
    return {
        # Metadata-gated (#455): presenting arbitrary markdown makes no sense,
        # so the deck must be *declared*.  v1 also offered it to anything that
        # happened to split into more than one slide, which handed a Present
        # button to any prose document containing two `---` rules.
        # The declaration is necessary but not sufficient — front matter that
        # swallows the whole file leaves nothing to present, and the button
        # would be dead.
        "available": directives.get("marp", "").lower() == "true" and bool(slides),
        # Lands in a CSS class name, so it may only ever be one of ours.
        "theme": theme if theme in PRESENTATION_THEMES else "default",
        "paginate": directives.get("paginate", "").lower() == "true",
        "slides": slides,
    }


def header_fields(data):
    """Header + comment-form fields for a document.

    The form fields must follow the file on screen, or comments would be
    posted against the previously viewed one.
    """
    return {
        "title": data["path"],
        "fileIdLabel": str(data["file_id"])[:12] + "\u2026",
        "documentTitle": "doc-review \u2014 " + data["path"],
        "formFileId": data["file_id"],
        "formPath": data["path"],
    }
