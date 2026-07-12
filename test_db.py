"""Tests for the SQLite comment data layer."""

import sqlite3
import tempfile
from pathlib import Path

import pytest

from db import (
    create_comment,
    get_comment,
    get_connection,
    get_replies,
    init_db,
    list_comments,
    resolve_comment,
    unresolve_comment,
)


@pytest.fixture
def conn():
    """In-memory SQLite connection with schema initialized."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    init_db(c)
    return c


def _make_comment(conn, **overrides):
    defaults = dict(
        file_id="abc123",
        line_start=10,
        line_end=10,
        author="tester",
        body="a comment",
        parent_id=None,
    )
    defaults.update(overrides)
    return create_comment(conn, **defaults)


class TestCreateComment:
    def test_basic_create(self, conn):
        c = _make_comment(conn)
        assert c["id"] == 1
        assert c["file_id"] == "abc123"
        assert c["line_start"] == 10
        assert c["line_end"] == 10
        assert c["author"] == "tester"
        assert c["body"] == "a comment"
        assert c["parent_id"] is None
        assert c["resolved"] == 0

    def test_auto_increment_id(self, conn):
        c1 = _make_comment(conn, body="first")
        c2 = _make_comment(conn, body="second")
        assert c2["id"] == c1["id"] + 1

    def test_timestamps_set(self, conn):
        c = _make_comment(conn)
        assert c["created_at"] is not None
        assert c["updated_at"] is not None


class TestListComments:
    def test_list_by_file_id(self, conn):
        _make_comment(conn, file_id="f1", line_start=1, line_end=1)
        _make_comment(conn, file_id="f1", line_start=5, line_end=5)
        _make_comment(conn, file_id="f2", line_start=1, line_end=1)
        result = list_comments(conn, "f1")
        assert len(result) == 2
        assert all(c["file_id"] == "f1" for c in result)

    def test_list_empty(self, conn):
        result = list_comments(conn, "nonexistent")
        assert result == []

    def test_filter_by_line_range(self, conn):
        _make_comment(conn, file_id="f1", line_start=1, line_end=3)
        _make_comment(conn, file_id="f1", line_start=5, line_end=7)
        _make_comment(conn, file_id="f1", line_start=10, line_end=12)
        # Query lines 4-8 — should match the comment on lines 5-7
        result = list_comments(conn, "f1", line_start=4, line_end=8)
        assert len(result) == 1
        assert result[0]["line_start"] == 5

    def test_filter_overlapping_ranges(self, conn):
        _make_comment(conn, file_id="f1", line_start=1, line_end=10)
        _make_comment(conn, file_id="f1", line_start=8, line_end=15)
        _make_comment(conn, file_id="f1", line_start=20, line_end=25)
        # Query lines 5-12 — should match first two
        result = list_comments(conn, "f1", line_start=5, line_end=12)
        assert len(result) == 2

    def test_exclude_resolved(self, conn):
        c1 = _make_comment(conn, file_id="f1", body="open")
        c2 = _make_comment(conn, file_id="f1", body="will resolve")
        resolve_comment(conn, c2["id"])
        result = list_comments(conn, "f1", include_resolved=False)
        assert len(result) == 1
        assert result[0]["body"] == "open"

    def test_include_resolved(self, conn):
        _make_comment(conn, file_id="f1", body="open")
        c2 = _make_comment(conn, file_id="f1", body="resolved")
        resolve_comment(conn, c2["id"])
        result = list_comments(conn, "f1", include_resolved=True)
        assert len(result) == 2


class TestResolveComment:
    def test_resolve(self, conn):
        c = _make_comment(conn)
        resolved = resolve_comment(conn, c["id"])
        assert resolved["resolved"] == 1

    def test_unresolve(self, conn):
        c = _make_comment(conn)
        resolve_comment(conn, c["id"])
        unresolved = unresolve_comment(conn, c["id"])
        assert unresolved["resolved"] == 0

    def test_resolve_nonexistent(self, conn):
        result = resolve_comment(conn, 9999)
        assert result is None


class TestGetComment:
    def test_get_existing(self, conn):
        c = _make_comment(conn)
        fetched = get_comment(conn, c["id"])
        assert fetched["id"] == c["id"]
        assert fetched["body"] == c["body"]

    def test_get_nonexistent(self, conn):
        assert get_comment(conn, 9999) is None


class TestReplyThreading:
    def test_create_reply(self, conn):
        parent = _make_comment(conn, body="parent")
        reply = _make_comment(conn, body="reply", parent_id=parent["id"])
        assert reply["parent_id"] == parent["id"]

    def test_get_replies(self, conn):
        parent = _make_comment(conn, body="parent")
        _make_comment(conn, body="reply1", parent_id=parent["id"])
        _make_comment(conn, body="reply2", parent_id=parent["id"])
        replies = get_replies(conn, parent["id"])
        assert len(replies) == 2
        assert replies[0]["body"] == "reply1"
        assert replies[1]["body"] == "reply2"

    def test_no_replies(self, conn):
        parent = _make_comment(conn)
        replies = get_replies(conn, parent["id"])
        assert replies == []


class TestFilePath:
    """Tests for the file_path column — schema migration and persistence."""

    def test_file_path_column_exists_new_db(self, conn):
        """New databases should have the file_path column."""
        cur = conn.execute("PRAGMA table_info(comments)")
        columns = {row[1] for row in cur.fetchall()}
        assert "file_path" in columns

    def test_file_path_stored_on_create(self, conn):
        """create_comment with file_path should persist it."""
        c = create_comment(
            conn,
            file_id="blob123",
            line_start=5,
            line_end=5,
            author="tester",
            body="path test",
            file_path="docs/readme.md",
        )
        assert c["file_path"] == "docs/readme.md"

    def test_file_path_nullable(self, conn):
        """create_comment without file_path should store NULL (backward-compat)."""
        c = _make_comment(conn)
        assert c["file_path"] is None

    def test_migrate_existing_db_adds_file_path(self):
        """An existing DB without file_path should gain the column after init_db."""
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "legacy.db"
            # Create a legacy DB without the file_path column
            legacy_conn = sqlite3.connect(str(db_path))
            legacy_conn.execute("""
                CREATE TABLE comments (
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
                )
            """)
            legacy_conn.execute("""
                INSERT INTO comments (file_id, line_start, line_end, author, body,
                                      parent_id, resolved, created_at, updated_at)
                VALUES ('old_blob', 1, 1, 'alice', 'old comment', NULL, 0,
                        '2026-01-01T00:00:00', '2026-01-01T00:00:00')
            """)
            legacy_conn.commit()
            legacy_conn.close()

            # Now open with our code — should migrate
            conn = get_connection(db_path)
            init_db(conn)

            # Column should exist
            cur = conn.execute("PRAGMA table_info(comments)")
            columns = {row[1] for row in cur.fetchall()}
            assert "file_path" in columns

            # Existing row should have file_path = NULL
            row = conn.execute("SELECT file_path FROM comments WHERE id=1").fetchone()
            assert row[0] is None

            conn.close()

    def test_file_path_index_exists(self, conn):
        """The idx_comments_file_path index should be created."""
        cur = conn.execute("PRAGMA index_list(comments)")
        index_names = {row[1] for row in cur.fetchall()}
        assert "idx_comments_file_path" in index_names


class TestGetConnection:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "test.db"
            conn = get_connection(db_path)
            init_db(conn)
            conn.close()
            assert db_path.exists()
