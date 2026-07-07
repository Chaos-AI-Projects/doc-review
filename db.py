"""SQLite data layer for line-anchored comments.

Schema
------
comments:
    id          INTEGER PRIMARY KEY
    file_id     TEXT NOT NULL        -- git blob id or content-hash fallback
    line_start  INTEGER NOT NULL     -- 1-based inclusive
    line_end    INTEGER NOT NULL     -- 1-based inclusive (= line_start for single-line)
    author      TEXT NOT NULL
    body        TEXT NOT NULL
    parent_id   INTEGER              -- NULL for top-level, FK for reply threading
    resolved    INTEGER NOT NULL DEFAULT 0
    created_at  TEXT NOT NULL        -- ISO-8601
    updated_at  TEXT NOT NULL        -- ISO-8601
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     TEXT    NOT NULL,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    author      TEXT    NOT NULL,
    body        TEXT    NOT NULL,
    parent_id   INTEGER,
    resolved    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    FOREIGN KEY (parent_id) REFERENCES comments(id)
);
CREATE INDEX IF NOT EXISTS idx_comments_file_lines
    ON comments (file_id, line_start, line_end);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str | Path = "comments.db") -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def create_comment(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    line_start: int,
    line_end: int,
    author: str,
    body: str,
    parent_id: int | None = None,
) -> dict:
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO comments (file_id, line_start, line_end, author, body,
                                 parent_id, resolved, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)""",
        (file_id, line_start, line_end, author, body, parent_id, now, now),
    )
    conn.commit()
    return dict(conn.execute("SELECT * FROM comments WHERE id=?", (cur.lastrowid,)).fetchone())


def list_comments(
    conn: sqlite3.Connection,
    file_id: str,
    *,
    line_start: int | None = None,
    line_end: int | None = None,
    include_resolved: bool = True,
) -> list[dict]:
    """List comments for a file, optionally filtered by line range."""
    query = "SELECT * FROM comments WHERE file_id = ?"
    params: list = [file_id]
    if line_start is not None:
        query += " AND line_end >= ?"
        params.append(line_start)
    if line_end is not None:
        query += " AND line_start <= ?"
        params.append(line_end)
    if not include_resolved:
        query += " AND resolved = 0"
    query += " ORDER BY line_start, created_at"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def resolve_comment(conn: sqlite3.Connection, comment_id: int) -> dict | None:
    now = _now_iso()
    conn.execute(
        "UPDATE comments SET resolved = 1, updated_at = ? WHERE id = ?",
        (now, comment_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    return dict(row) if row else None


def unresolve_comment(conn: sqlite3.Connection, comment_id: int) -> dict | None:
    now = _now_iso()
    conn.execute(
        "UPDATE comments SET resolved = 0, updated_at = ? WHERE id = ?",
        (now, comment_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    return dict(row) if row else None


def get_comment(conn: sqlite3.Connection, comment_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    return dict(row) if row else None


def get_replies(conn: sqlite3.Connection, comment_id: int) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT * FROM comments WHERE parent_id = ? ORDER BY created_at",
            (comment_id,),
        ).fetchall()
    ]
