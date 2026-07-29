"""Canonical parity-test fixture for anchor-range verification.

Shared between:
- ``test_parity.py`` (server-side assertion)
- ``GET /api/parity-fixture`` (served to the browser-side Pyodide check)

The fixture covers every markdown block type used by doc-review's renderer.
If you add a block type here, update EXPECTED_RANGES and re-run both the
pytest suite and the browser parity check.
"""

PARITY_FIXTURE = """\
# Title

A paragraph with **bold** and `code`.

- bullet one
- bullet two

> A blockquote

```python
x = 1
y = 2
```

| Col A | Col B |
|-------|-------|
| 1     | 2     |

---

## Section Two

Final paragraph.
"""

# Canonical block→line ranges (1-based inclusive) produced by renderer.py
# under CPython.  The Pyodide side MUST reproduce these exactly.
EXPECTED_RANGES = [
    (1, 1),    # h1: "# Title"
    (3, 3),    # paragraph
    (5, 7),    # bullet list (markdown-it includes trailing blank)
    (8, 8),    # blockquote
    (10, 13),  # fenced code block
    (15, 17),  # table
    (19, 19),  # horizontal rule
    (21, 21),  # h2: "## Section Two"
    (23, 23),  # paragraph: "Final paragraph."
]
