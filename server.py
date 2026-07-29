"""doc-review: line-anchored markdown review web app.

Usage:
    python server.py <path-to-file-or-directory>

Serves on 127.0.0.1:28080 by default (override with --host / --port).
"""

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from db import (
    create_comment,
    get_connection,
    init_db,
    list_comments,
    list_comments_by_path,
    resolve_comment,
    unresolve_comment,
    update_comment_anchor,
)
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


@functools.cache
def static_version(filename: str) -> str:
    """Return an 8-char content hash for a file under static/.

    Results are cached for the process lifetime.  A server restart is
    required after deploying new static assets so the hashes update.
    """
    path = (BASE_DIR / "static" / filename).resolve()
    if not path.is_relative_to(BASE_DIR / "static"):
        raise ValueError(f"Illegal static filename: {filename}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


templates.env.globals["static_version"] = static_version


@app.middleware("http")
async def add_static_cache_control(request: Request, call_next):
    """Add Cache-Control: no-cache to /static responses."""
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response

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


# ── Git anchor helpers (#406) ───────────────────────────────────────────
#
# All git calls run against the repo containing the *served file* via
# `git -C <file dir>`, never the server process cwd (the cwd mistake was
# the exact bug documented in #404).

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)")


def _git_head_if_clean(target: Path) -> str | None:
    """Return HEAD of the repo containing *target* when the file is
    git-tracked and clean vs HEAD; otherwise None."""
    d = str(target.parent)
    name = target.name
    try:
        tracked = subprocess.run(
            ["git", "-C", d, "ls-files", "--error-unmatch", name],
            capture_output=True, timeout=5,
        )
        if tracked.returncode != 0:
            return None
        clean = subprocess.run(
            ["git", "-C", d, "diff", "--quiet", "HEAD", "--", name],
            capture_output=True, timeout=5,
        )
        if clean.returncode != 0:
            return None
        head = subprocess.run(
            ["git", "-C", d, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if head.returncode != 0:
            return None
        return head.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _blame_surviving_lines(
    target: Path, anchor_commit: str, head: str, line_start: int, line_end: int
) -> list[int] | None:
    """Map *line_start..line_end* (valid at *anchor_commit*) to the line
    numbers those lines occupy in *head*, via reverse blame.

    Returns the surviving line numbers in HEAD ([] when the whole range was
    deleted), or None when blame itself failed (caller should leave the
    stored anchor untouched).
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", str(target.parent),
                "blame", "--reverse", "--porcelain",
                f"{anchor_commit}..HEAD",
                "-L", f"{line_start},{line_end}",
                "--", target.name,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    survivors = []
    for line in result.stdout.splitlines():
        m = _BLAME_HEADER_RE.match(line)
        # Reverse blame attributes a line to the last commit in which it
        # existed; lines surviving to HEAD are attributed to HEAD itself,
        # with the header's second field being the line number in HEAD.
        if m and m.group(1) == head:
            survivors.append(int(m.group(2)))
    return survivors


def _migrate_comment_anchors(
    conn, comments: list[dict], target: Path, total_lines: int
) -> None:
    """Re-anchor comments whose anchor_commit lags the file's current HEAD.

    Mutates *comments* in place and persists updates, so blame runs once per
    edit rather than on every view.  Skipped entirely when the file is
    untracked or has uncommitted changes (dirty tree: serve stored anchors).
    Legacy rows (anchor_commit NULL) are never touched.
    """
    head: str | None = None
    head_checked = False
    for c in comments:
        anchor = c.get("anchor_commit")
        if not anchor:
            continue
        if not head_checked:
            head = _git_head_if_clean(target)
            head_checked = True
        if head is None or anchor == head:
            continue
        survivors = _blame_surviving_lines(
            target, anchor, head, c["line_start"], c["line_end"]
        )
        if survivors is None:
            continue  # git failure — serve stored anchors unchanged
        if survivors:
            new_start, new_end = min(survivors), max(survivors)
        else:
            # Whole range deleted: clamp to current file length so the
            # comment stays visible on the nearest block.
            new_start = max(1, min(c["line_start"], total_lines))
            new_end = max(1, min(c["line_end"], total_lines))
        update_comment_anchor(
            conn, c["id"],
            line_start=new_start, line_end=new_end, anchor_commit=head,
        )
        c["line_start"], c["line_end"], c["anchor_commit"] = new_start, new_end, head


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
        # Look up comments by file_path — the stable identity across edits
        # (#404). Merge in legacy rows (file_path IS NULL) that match the
        # current content id, deduped by comment id.
        comments = list_comments_by_path(conn, path)
        seen_ids = {c["id"] for c in comments}
        legacy = [
            c
            for c in list_comments(conn, fid)
            if c["file_path"] is None and c["id"] not in seen_ids
        ]
        comments = comments + legacy

        # Re-anchor comments whose anchor_commit lags the file's repo HEAD
        # (#406) — must happen before sorting/grouping by line_start.
        _migrate_comment_anchors(
            conn, comments, file_path, len(source.splitlines())
        )

        comments = sorted(
            comments,
            key=lambda c: (c["line_start"], c["created_at"]),
        )

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
    target = _resolve_file(path)  # validate path is inside source root
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
            anchor_commit=_git_head_if_clean(target),
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


# ── JSON API (#435) ───────────────────────────────────────────────────


@app.get("/api/comments")
async def api_get_comments(path: str = Query(...)):
    """Return JSON list of comments for a file."""
    _resolve_file(path)  # validates path is inside source root, returns 403/404
    conn = _conn()
    try:
        comments = list_comments_by_path(conn, path)
        fid = derive_file_id(str(_resolve_file(path)))
        seen_ids = {c["id"] for c in comments}
        legacy = [
            c for c in list_comments(conn, fid)
            if c["file_path"] is None and c["id"] not in seen_ids
        ]
        comments = comments + legacy
        comments.sort(key=lambda c: (c["line_start"], c["created_at"]))
    finally:
        conn.close()
    return JSONResponse(content=comments)


class _CommentCreate(BaseModel):
    file_id: str
    path: str
    line_start: int
    line_end: int
    author: str = "anon"
    body: str
    parent_id: int | None = None


@app.post("/api/comments", status_code=201)
async def api_post_comment(payload: _CommentCreate):
    """Create a comment from a JSON body, return the created comment as JSON."""
    target = _resolve_file(payload.path)
    conn = _conn()
    try:
        comment = create_comment(
            conn,
            file_id=payload.file_id,
            line_start=payload.line_start,
            line_end=payload.line_end,
            author=payload.author,
            body=payload.body,
            parent_id=payload.parent_id if payload.parent_id and payload.parent_id > 0 else None,
            file_path=payload.path,
            anchor_commit=_git_head_if_clean(target),
        )
    finally:
        conn.close()
    return JSONResponse(content=comment, status_code=201)


# ── Blame JSON API (#443 spike) ──────────────────────────────────────────

@app.get("/api/blame")
async def api_blame(path: str = Query(...)):
    """Return per-line git-blame data as JSON for a tracked file.

    Response: ``{"lines": [{"line": int, "commit": str, "author": str,
    "date": str, "content": str}, ...]}``
    """
    target = _resolve_file(path)
    d = str(target.parent)
    name = target.name
    try:
        result = subprocess.run(
            ["git", "-C", d, "blame", "--porcelain", "--", name],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise HTTPException(status_code=502, detail="git blame unavailable")
    if result.returncode != 0:
        raise HTTPException(
            status_code=404,
            detail="File is not git-tracked or blame failed",
        )

    # Parse porcelain output into per-line records.
    lines: list[dict] = []
    current_commit = ""
    commit_meta: dict[str, dict] = {}  # commit -> {author, date}
    line_num = 0
    for raw_line in result.stdout.splitlines():
        m = _BLAME_HEADER_RE.match(raw_line)
        if m:
            current_commit = m.group(1)
            line_num = int(m.group(3))
            if current_commit not in commit_meta:
                commit_meta[current_commit] = {"author": "", "date": ""}
        elif raw_line.startswith("author "):
            commit_meta[current_commit]["author"] = raw_line[7:]
        elif raw_line.startswith("author-time "):
            commit_meta[current_commit]["date"] = raw_line[12:]
        elif raw_line.startswith("\t"):
            meta = commit_meta.get(current_commit, {"author": "", "date": ""})
            lines.append({
                "line": line_num,
                "commit": current_commit[:12],
                "author": meta["author"],
                "date": meta["date"],
                "content": raw_line[1:],
            })

    return JSONResponse(content={"lines": lines})


# ── Render / parity-fixture API (#443 spike) ─────────────────────────────


@app.post("/api/render")
async def api_render(request: Request):
    """Render markdown source and return block ranges as JSON.

    Request body: ``{"source": "<markdown text>"}``
    Response: ``{"blocks": [{"start_line": int, "end_line": int}, ...]}``
    """
    body = await request.json()
    source = body.get("source", "")
    blocks = render_markdown_blocks(source)
    return JSONResponse(content={
        "blocks": [
            {"start_line": b["start_line"], "end_line": b["end_line"]}
            for b in blocks
        ]
    })


@app.get("/api/parity-fixture")
async def api_parity_fixture():
    """Return the canonical parity-test fixture and its expected ranges.

    Used by the Pyodide preview page to run the client-side parity check
    against the same fixture used by test_parity.py.
    """
    from parity_fixture import EXPECTED_RANGES, PARITY_FIXTURE

    return JSONResponse(content={
        "source": PARITY_FIXTURE,
        "expected_ranges": [
            {"start_line": s, "end_line": e} for s, e in EXPECTED_RANGES
        ],
    })


# ── Pyodide spike preview (#443) ─────────────────────────────────────────


@app.get("/spike/preview", response_class=HTMLResponse)
async def spike_preview(request: Request):
    """Pyodide-powered in-browser markdown preview (spike page)."""
    return templates.TemplateResponse(
        request,
        "spike_preview.html",
        context={},
    )


@app.get("/spike/renderer.py")
async def spike_renderer_source():
    """Serve renderer.py source for Pyodide to load verbatim."""
    source = (BASE_DIR / "renderer.py").read_text(encoding="utf-8")
    return JSONResponse(content={"source": source})


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
