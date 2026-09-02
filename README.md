# doc-review

Block-anchored markdown review web app. Lets a reviewer read markdown source files in the browser and attach comments anchored to document blocks (headings, paragraphs, lists, tables, code blocks).

## Quick Reference

```bash
# Install dependencies (Python 3.10+), declared in pyproject.toml.
# Every command in this block is run from the monorepo root.
pip install -e doc-review

# Add the test dependencies as well
pip install -e "doc-review[dev]"

# Run server (serve a directory for review)
python doc-review/server.py /path/to/docs --host 127.0.0.1 --port 28080

# Run tests
cd doc-review && pytest -v
```

## Architecture

- **Server:** FastAPI, server-rendered HTML (Jinja2), minimal vanilla JS
- **Storage:** SQLite (`comments.db`) for block-anchored comments
- **File identity:** git blob object id for git-tracked files; path+content SHA-256 fallback
- **Comment anchoring:** every block carries a content-derived `block_id` (`sha256` of its normalized source plus an occurrence index), stored with the comment. On every read — the web view and `GET /api/comments` alike, and so `comments_cli.py` — a comment is placed by `block_id` first, then by the `anchor_commit` reverse-blame migration, then by its stored line numbers — so a comment follows its text even on a dirty tree or a non-git root. A comment whose block is gone is still shown, flagged *detached*, never silently reattached to unrelated text
- **Rendering:** Markdown source parsed by markdown-it-py into blocks, rendered to HTML with per-block anchors and TOC
- **Presentation mode:** Marp-shaped markdown (`---` slide breaks + front-matter directives) presented as slides — a client-side grouping of the *same* blocks review mode renders, so comments keep their anchors across a mode flip. Offered only for a document whose front matter declares `marp: true`. Opens full screen where the browser allows it, falling back to a fixed overlay otherwise. Read-only; `Esc` or the on-screen exit control returns to review, arrow keys or the on-screen arrows move between slides
- **Responsive:** Desktop = sidebar; mobile = inline expandable panels

## Project Structure

```
doc-review/
├── server.py          # FastAPI app + CLI entrypoint
├── db.py              # SQLite data layer (comments CRUD)
├── file_id.py         # File identity derivation (git blob / content hash)
├── renderer.py        # Markdown → per-block HTML renderer (markdown-it-py)
├── view_specs.py      # Row/TOC/header render specs — shared by the Jinja
│                      # render and the in-browser Pyodide soft swap
├── templates/         # Jinja2 templates
│   ├── base.html
│   ├── index.html     # File browser
│   └── view.html      # File viewer with comment UI
├── static/
│   ├── style.css      # Responsive CSS
│   └── app.js         # Minimal JS for comment interaction
├── test_db.py         # Data layer tests
├── test_file_id.py    # File ID derivation tests
├── test_renderer.py   # Renderer tests
├── test_presentation.py # Marp presentation mode + anchor-parity tests
├── test_view_specs.py # Render-spec builder tests
├── test_server.py     # Route-level tests
└── README.md          # This file
```

## Assumptions

- **Markdown-only in v1.** Org-mode rendering deferred to a follow-up.
- **File/directory selection** via CLI argument to `server.py`.
- **No auth** in first cut — single-user / trusted-network deployment.
