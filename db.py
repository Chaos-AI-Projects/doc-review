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
    file_path   TEXT                 -- relative path at comment-creation time (nullable for legacy)
    anchor_commit TEXT               -- commit of the served file's repo at which
                                     -- line_start/line_end were last known correct
                                     -- (NULL: untracked/dirty at creation, or legacy)
    block_id    TEXT                 -- content-derived id of the commented block
                                     -- (#465; NULL for legacy rows, backfilled on
                                     -- the first successful line-based resolution)
    block_context TEXT               -- digest of the block preceding the commented one
                                     -- when it was written (#467), or '' for the first
                                     -- block.  Tells identical blocks apart, where the
                                     -- occurrence number in block_id cannot: it does not
                                     -- move when a copy is inserted above or an earlier
                                     -- copy is deleted.  NULL for legacy rows, which keep
                                     -- resolving by occurrence.
    block_offset INTEGER             -- how many lines into its block the comment began
                                     -- when it was written, so it can be placed inside
                                     -- the block wherever the block now sits (#465
                                     -- review; NULL for legacy rows, written alongside
                                     -- block_id).  An offset rather than the block's
                                     -- start line because the blame migration rewrites
                                     -- line_start, which would leave a stored start
                                     -- line stale and double-count the displacement.
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
    file_path   TEXT,
    anchor_commit TEXT,
    block_id    TEXT,
    block_offset INTEGER,
    block_context TEXT,
    FOREIGN KEY (parent_id) REFERENCES comments(id)
);
CREATE INDEX IF NOT EXISTS idx_comments_file_lines
    ON comments (file_id, line_start, line_end);
CREATE INDEX IF NOT EXISTS idx_comments_file_path
    ON comments (file_path);
CREATE INDEX IF NOT EXISTS idx_comments_block_id
    ON comments (block_id);
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
    # Check if the table already exists (for migration path)
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='comments'"
    ).fetchone()

    if not table_exists:
        # Fresh DB — create with full schema including file_path
        conn.executescript(_SCHEMA)
    else:
        # Existing table — migrate: add columns if missing.  Additive only:
        # comments.db holds real review history, so the table is never dropped
        # or rewritten.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(comments)").fetchall()}
        if "file_path" not in cols:
            conn.execute("ALTER TABLE comments ADD COLUMN file_path TEXT")
        if "anchor_commit" not in cols:
            conn.execute("ALTER TABLE comments ADD COLUMN anchor_commit TEXT")
        if "block_id" not in cols:
            conn.execute("ALTER TABLE comments ADD COLUMN block_id TEXT")
        if "block_offset" not in cols:
            conn.execute("ALTER TABLE comments ADD COLUMN block_offset INTEGER")
        if "block_context" not in cols:
            conn.execute("ALTER TABLE comments ADD COLUMN block_context TEXT")
        # Ensure all indexes exist
        conn.executescript(
            "CREATE INDEX IF NOT EXISTS idx_comments_file_lines"
            "    ON comments (file_id, line_start, line_end);\n"
            "CREATE INDEX IF NOT EXISTS idx_comments_file_path"
            "    ON comments (file_path);\n"
            "CREATE INDEX IF NOT EXISTS idx_comments_block_id"
            "    ON comments (block_id);\n"
        )
        conn.commit()


def create_comment(
    conn: sqlite3.Connection,
    *,
    file_id: str,
    line_start: int,
    line_end: int,
    author: str,
    body: str,
    parent_id: int | None = None,
    file_path: str | None = None,
    anchor_commit: str | None = None,
    block_id: str | None = None,
    block_offset: int | None = None,
    block_context: str | None = None,
) -> dict:
    now = _now_iso()
    cur = conn.execute(
        """INSERT INTO comments (file_id, line_start, line_end, author, body,
                                 parent_id, resolved, created_at, updated_at,
                                 file_path, anchor_commit, block_id, block_offset,
                                 block_context)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)""",
        (file_id, line_start, line_end, author, body, parent_id, now, now,
         file_path, anchor_commit, block_id, block_offset, block_context),
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


def list_comments_by_path(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    include_resolved: bool = True,
) -> list[dict]:
    """List comments for a file by its stored file_path (fast indexed query)."""
    query = "SELECT * FROM comments WHERE file_path = ?"
    params: list = [file_path]
    if not include_resolved:
        query += " AND resolved = 0"
    query += " ORDER BY line_start, created_at"
    return [dict(row) for row in conn.execute(query, params).fetchall()]


def update_comment_anchor(
    conn: sqlite3.Connection,
    comment_id: int,
    *,
    line_start: int,
    line_end: int,
    anchor_commit: str,
) -> None:
    """Persist a migrated line anchor (see #406: reverse-blame migration)."""
    conn.execute(
        """UPDATE comments
           SET line_start = ?, line_end = ?, anchor_commit = ?, updated_at = ?
           WHERE id = ?""",
        (line_start, line_end, anchor_commit, _now_iso(), comment_id),
    )
    conn.commit()


def set_comment_block_id(
    conn: sqlite3.Connection,
    comment_id: int,
    block_id: str,
    block_offset: int | None,
    block_context: str | None = None,
) -> None:
    """Backfill the block anchor of a pre-#465 row.

    Only the block anchor is written: learning a comment's block is bookkeeping,
    not an edit, so the line anchor and ``updated_at`` stay as the author left
    them.  ``block_offset`` is written with ``block_id`` because the pair is what
    places the comment: the id says which block, the offset says how far into it.
    It is ``int | None`` rather than ``int`` because the backfill passes
    ``normalized_offset()``, which is None for a block that normalizes to
    nothing and so has no line to name.  That case still gets its ``block_id``:
    the id is what places the comment, and NULL is stored rather than 0, which
    is a real offset meaning "the block's first line".

    ``block_context`` (#467) is written with them for the same reason: an id
    resolved on a document that holds several identical blocks names the copy it
    was resolved against only while nothing is reordered, and the context is what
    keeps naming it afterwards.  It defaults to ``None`` so a caller that has not
    got one leaves the column NULL rather than storing a context of ``''``, which
    is a real value meaning "first block in the document".
    """
    conn.execute(
        "UPDATE comments SET block_id = ?, block_offset = ?, block_context = ? "
        "WHERE id = ?",
        (block_id, block_offset, block_context, comment_id),
    )
    conn.commit()


def latest_comment_in_block(
    conn: sqlite3.Connection,
    *,
    block_id: str,
    file_path: str,
    file_id: str,
) -> dict | None:
    """The most recent comment already written on this block (#465 Phase 2).

    Scoped exactly the way the view collects a file's comments: rows carrying
    this ``file_path``, plus legacy rows that predate the column and match by
    content id.  A ``block_id`` is derived from block *content*, so the same
    boilerplate in two documents shares one — the file scope is what keeps
    their threads apart.

    Ordered by ``id`` after ``created_at`` because two comments written in the
    same microsecond would otherwise tie, and "the latest" has to be one row.
    """
    row = conn.execute(
        """SELECT * FROM comments
            WHERE block_id = ?
              AND (file_path = ? OR (file_path IS NULL AND file_id = ?))
            ORDER BY created_at DESC, id DESC
            LIMIT 1""",
        (block_id, file_path, file_id),
    ).fetchone()
    return dict(row) if row else None


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
