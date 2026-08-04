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
