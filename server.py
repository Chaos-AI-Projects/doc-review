"""doc-review: line-anchored markdown review web app.

Usage:
    python server.py <path-to-file-or-directory>

Serves on 127.0.0.1:28080 by default (override with --host / --port).
"""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from db import create_comment, get_connection, get_replies, init_db, list_comments, resolve_comment, unresolve_comment
from file_id import derive_file_id
from renderer import render_markdown_lines

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="doc-review")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _safe_tojson(value: object) -> Markup:
    """Serialize to JSON and escape </script> to prevent HTML parser breakout."""
    s = json.dumps(value, ensure_ascii=False)
    s = s.replace("<", "\\u003c")
    return Markup(s)


templates.env.filters["safe_tojson"] = _safe_tojson

# Globals set by configure()
_db_path: str = "comments.db"
_source_root: Path = Path(".")


def configure(source_root: str | Path, db_path: str | Path = "comments.db") -> None:
    """Set the source root and DB path.  Called before the server starts."""
    global _db_path, _source_root
    _source_root = Path(source_root).resolve()
    _db_path = str(db_path)
    conn = get_connection(_db_path)
    init_db(conn)
    conn.close()


def _conn():
    return get_connection(_db_path)


def _resolve_file(rel_path: str) -> Path:
    """Resolve *rel_path* against the source root, ensuring it stays inside."""
    target = (_source_root / rel_path).resolve()
    if not target.is_relative_to(_source_root):
        raise HTTPException(status_code=403, detail="Path traversal denied")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


def _list_files(root: Path) -> list[str]:
    """Recursively list markdown files under *root* as relative paths."""
    exts = {".md", ".markdown"}
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            files.append(str(p.relative_to(root)))
    return files


# ── Routes ──────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Directory browser / file list."""
    files = _list_files(_source_root)
    return templates.TemplateResponse(
        request,
        "index.html",
        context={"files": files, "root": str(_source_root)},
    )


@app.get("/view", response_class=HTMLResponse)
async def view_file(request: Request, path: str = Query(...)):
    """Render a file with per-line anchors and comments."""
    file_path = _resolve_file(path)
    source = file_path.read_text(encoding="utf-8", errors="replace")
    lines = render_markdown_lines(source)
    fid = derive_file_id(str(file_path))

    conn = _conn()
    try:
        comments = list_comments(conn, fid)

        # Group comments by line_start for sidebar display.
        comments_by_line: dict[int, list[dict]] = {}
        for c in comments:
            comments_by_line.setdefault(c["line_start"], []).append(c)

        # Build reply map
        reply_map: dict[int, list[dict]] = {}
        for c in comments:
            if c["parent_id"] is None:
                reply_map[c["id"]] = get_replies(conn, c["id"])
    finally:
        conn.close()

    all_files = _list_files(_source_root)

    return templates.TemplateResponse(
        request,
        "view.html",
        context={
            "path": path,
            "file_id": fid,
            "lines": lines,
            "comments_by_line": comments_by_line,
            "reply_map": reply_map,
            "files": all_files,
        },
    )


@app.post("/comment")
async def add_comment(
    file_id: str = Form(...),
    path: str = Form(...),
    line_start: int = Form(...),
    line_end: int = Form(...),
    author: str = Form("anon"),
    body: str = Form(...),
    parent_id: int | None = Form(None),
):
    """Create a comment (or reply) and redirect back to the file view."""
    _resolve_file(path)  # validate path is inside source root
    conn = _conn()
    try:
        create_comment(
            conn,
            file_id=file_id,
            line_start=line_start,
            line_end=line_end,
            author=author,
            body=body,
            parent_id=parent_id if parent_id and parent_id > 0 else None,
        )
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/view?path={quote(path)}#L{line_start}",
        status_code=303,
    )


@app.post("/comment/{comment_id}/resolve")
async def resolve(comment_id: int, path: str = Form(...)):
    _resolve_file(path)
    conn = _conn()
    try:
        resolve_comment(conn, comment_id)
    finally:
        conn.close()
    return RedirectResponse(url=f"/view?path={quote(path)}", status_code=303)


@app.post("/comment/{comment_id}/unresolve")
async def unresolve(comment_id: int, path: str = Form(...)):
    _resolve_file(path)
    conn = _conn()
    try:
        unresolve_comment(conn, comment_id)
    finally:
        conn.close()
    return RedirectResponse(url=f"/view?path={quote(path)}", status_code=303)


# ── Entrypoint ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="doc-review server")
    parser.add_argument("source", help="File or directory to serve for review")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28080)
    parser.add_argument("--db", default="comments.db", help="SQLite DB path")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        parser.error(f"Source path does not exist: {source}")

    # If a single file is given, serve its parent directory.
    if source.is_file():
        configure(source.parent, args.db)
    else:
        configure(source, args.db)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
