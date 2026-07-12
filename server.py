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

from db import create_comment, get_connection, init_db, list_comments, resolve_comment, unresolve_comment
from file_id import derive_file_id
from renderer import extract_toc, render_markdown_blocks

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
    resp = templates.TemplateResponse(
        request,
        "index.html",
        context={"files": files, "root": str(_source_root)},
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/view", response_class=HTMLResponse)
async def view_file(request: Request, path: str = Query(...)):
    """Render a file with per-line anchors and comments."""
    file_path = _resolve_file(path)
    source = file_path.read_text(encoding="utf-8", errors="replace")
    blocks = render_markdown_blocks(source)
    toc = extract_toc(source)
    fid = derive_file_id(str(file_path))

    conn = _conn()
    try:
        comments = list_comments(conn, fid)

        # Map each source line to its containing block's start_line.
        line_to_block_start: dict[int, int] = {}
        for block in blocks:
            for ln in range(block["start_line"], block["end_line"] + 1):
                line_to_block_start[ln] = block["start_line"]

        # Group ALL comments by their containing block's start_line.
        # Flat thread model: no nesting, no reply_map — every comment
        # (regardless of parent_id) appears in the block's time-ordered list.
        comments_by_block: dict[int, list[dict]] = {}
        for c in comments:
            block_start = line_to_block_start.get(c["line_start"], c["line_start"])
            comments_by_block.setdefault(block_start, []).append(c)
    finally:
        conn.close()

    all_files = _list_files(_source_root)

    resp = templates.TemplateResponse(
        request,
        "view.html",
        context={
            "path": path,
            "file_id": fid,
            "blocks": blocks,
            "toc": toc,
            "comments_by_block": comments_by_block,
            "files": all_files,
        },
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


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
            file_path=path,
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
