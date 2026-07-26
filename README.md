# doc-review

Block-anchored markdown review web app. Lets a reviewer read markdown source files in the browser and attach comments anchored to document blocks (headings, paragraphs, lists, tables, code blocks).

## Quick Reference

```bash
# Install dependencies (Python 3.10+)
pip install fastapi uvicorn jinja2 markdown-it-py httpx python-multipart

# Run server (serve a directory for review)
python doc-review/server.py /path/to/docs --host 127.0.0.1 --port 28080

# Run tests
cd doc-review && pytest -v
```

## Architecture

- **Server:** FastAPI, server-rendered HTML (Jinja2), minimal vanilla JS
- **Storage:** SQLite (`comments.db`) for block-anchored comments
- **File identity:** git blob object id for git-tracked files; path+content SHA-256 fallback
- **Rendering:** Markdown source parsed by markdown-it-py into blocks, rendered to HTML with per-block anchors and TOC
- **Responsive:** Desktop = sidebar; mobile = inline expandable panels

## Project Structure

```
doc-review/
├── server.py          # FastAPI app + CLI entrypoint
├── db.py              # SQLite data layer (comments CRUD)
├── file_id.py         # File identity derivation (git blob / content hash)
├── renderer.py        # Markdown → per-block HTML renderer (markdown-it-py)
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
├── test_server.py     # Route-level tests
└── README.md          # This file
```

## Assumptions

- **Markdown-only in v1.** Org-mode rendering deferred to a follow-up.
- **File/directory selection** via CLI argument to `server.py`.
- **No auth** in first cut — single-user / trusted-network deployment.
