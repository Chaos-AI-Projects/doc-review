"""Tests for list_comments CLI — cross-version comment listing with git blame line mapping.

Uses a temporary git repo + temporary comments.db to exercise:
- Comment on current working-tree blob passes through with stored line unchanged
- Comment anchored to an OLD blob translates to the correct CURRENT line via git blame
- A deleted line maps to 'removed'
- Untracked/content-hash fallback works
- --repo resolution works
- --json output mode
- --open-only filtering
"""

import subprocess
from pathlib import Path

import pytest

from db import create_comment, get_connection, init_db
from list_comments import collect_comments


def _run_git(repo, *args):
    """Run a git command in the given repo directory."""
    result = subprocess.run(
        ["git"] + list(args),
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


def _blob_id(repo, path):
    """Get git blob hash for a file."""
    return subprocess.run(
        ["git", "hash-object", str(path)],
        cwd=str(repo),
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()


@pytest.fixture
def temp_repo(tmp_path):
    """Create a temporary git repo with initial commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.email", "test@test.com")
    _run_git(repo, "config", "user.name", "Test")
    # Initial commit so we have a HEAD
    (repo / ".gitkeep").touch()
    _run_git(repo, "add", ".gitkeep")
    _run_git(repo, "commit", "-m", "initial")
    return repo


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary comments.db with schema initialized."""
    db_path = tmp_path / "comments.db"
    conn = get_connection(db_path)
    init_db(conn)
    return db_path, conn


class TestCurrentVersionPassthrough:
    """Comments on the current working-tree blob pass through with stored lines."""

    def test_current_blob_no_translation(self, temp_repo, temp_db):
        db_path, conn = temp_db
        # Create and commit a file
        doc = temp_repo / "doc.md"
        doc.write_text("line1\nline2\nline3\nline4\nline5\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "add doc")

        current_blob = _blob_id(temp_repo, doc)
        create_comment(conn, file_id=current_blob, line_start=2, line_end=3,
                       author="alice", body="comment on current version")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1
        c = results[0]
        assert c["current_line_start"] == 2
        assert c["current_line_end"] == 3
        assert c["status"] != "removed"
        assert c["body"] == "comment on current version"


class TestOldVersionLineTranslation:
    """Comments anchored to older blobs are forward-mapped via git blame."""

    def test_shifted_lines(self, temp_repo, temp_db):
        db_path, conn = temp_db
        # Version 1: 5 lines
        doc = temp_repo / "doc.md"
        doc.write_text("line1\nline2\nline3\nline4\nline5\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v1")

        old_blob = _blob_id(temp_repo, doc)

        # Add comment on line 3 in the old version
        create_comment(conn, file_id=old_blob, line_start=3, line_end=3,
                       author="bob", body="comment on old line 3")

        # Version 2: insert 2 lines at the top, pushing old line 3 -> line 5
        doc.write_text("new1\nnew2\nline1\nline2\nline3\nline4\nline5\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v2 - insert lines at top")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1
        c = results[0]
        assert c["current_line_start"] == 5, f"Expected line 5, got {c['current_line_start']}"
        assert c["status"] != "removed"

    def test_multiple_versions(self, temp_repo, temp_db):
        """Comment from version 1 should forward-map through multiple commits."""
        db_path, conn = temp_db
        doc = temp_repo / "doc.md"

        # V1
        doc.write_text("aaa\nbbb\nccc\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v1")
        v1_blob = _blob_id(temp_repo, doc)

        create_comment(conn, file_id=v1_blob, line_start=2, line_end=2,
                       author="carol", body="comment on bbb")

        # V2: insert at top
        doc.write_text("xxx\naaa\nbbb\nccc\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v2")

        # V3: insert more at top
        doc.write_text("yyy\nxxx\naaa\nbbb\nccc\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v3")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1
        c = results[0]
        # bbb started at line 2, shifted to 3 in v2, then to 4 in v3
        assert c["current_line_start"] == 4


class TestDeletedLines:
    """Lines deleted in a later version should be marked 'removed'."""

    def test_deleted_line_marked_removed(self, temp_repo, temp_db):
        db_path, conn = temp_db
        doc = temp_repo / "doc.md"

        # V1: 3 lines
        doc.write_text("keep1\ndelete_me\nkeep2\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v1")
        v1_blob = _blob_id(temp_repo, doc)

        create_comment(conn, file_id=v1_blob, line_start=2, line_end=2,
                       author="dave", body="comment on deleted line")

        # V2: remove line 2
        doc.write_text("keep1\nkeep2\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "v2 - remove line")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1
        c = results[0]
        assert c["status"] == "removed"


class TestUntrackedFallback:
    """Comments on untracked files (content-hash fallback) should work."""

    def test_content_hash_comment(self, temp_repo, temp_db):
        db_path, conn = temp_db
        # Create a file but don't add/commit it
        doc = temp_repo / "untracked.md"
        doc.write_text("line1\nline2\nline3\n")

        # Compute content-hash the same way file_id.py does
        import hashlib
        abs_path = str(doc.resolve())
        content = doc.read_bytes()
        h = hashlib.sha256()
        h.update(abs_path.encode())
        h.update(content)
        content_hash = h.hexdigest()

        create_comment(conn, file_id=content_hash, line_start=1, line_end=1,
                       author="eve", body="comment on untracked file")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1
        c = results[0]
        assert c["current_line_start"] == 1
        assert c["body"] == "comment on untracked file"


class TestRepoResolution:
    """--repo flag should work for locating the git repo."""

    def test_explicit_repo_path(self, temp_repo, temp_db):
        db_path, conn = temp_db
        doc = temp_repo / "doc.md"
        doc.write_text("hello\nworld\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "add doc")

        blob = _blob_id(temp_repo, doc)
        create_comment(conn, file_id=blob, line_start=1, line_end=1,
                       author="frank", body="repo resolution test")

        # Call with explicit repo path
        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1


class TestFiltering:
    """Test --open-only / --include-resolved filtering."""

    def test_open_only_excludes_resolved(self, temp_repo, temp_db):
        db_path, conn = temp_db
        doc = temp_repo / "doc.md"
        doc.write_text("line1\nline2\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "add doc")

        blob = _blob_id(temp_repo, doc)
        c1 = create_comment(conn, file_id=blob, line_start=1, line_end=1,
                            author="grace", body="open comment")
        c2 = create_comment(conn, file_id=blob, line_start=2, line_end=2,
                            author="grace", body="resolved comment")
        conn.execute("UPDATE comments SET resolved = 1 WHERE id = ?", (c2["id"],))
        conn.commit()

        results_all = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path),
            include_resolved=True,
        )
        assert len(results_all) == 2

        results_open = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path),
            include_resolved=False,
        )
        assert len(results_open) == 1
        assert results_open[0]["body"] == "open comment"


class TestNoComments:
    """Files with no comments should return an empty list."""

    def test_empty_results(self, temp_repo, temp_db):
        db_path, conn = temp_db
        doc = temp_repo / "doc.md"
        doc.write_text("no comments here\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "add doc")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert results == []


class TestJsonOutput:
    """--json output should be valid JSON with expected fields."""

    def test_json_output_fields(self, temp_repo, temp_db):
        db_path, conn = temp_db
        doc = temp_repo / "doc.md"
        doc.write_text("line1\nline2\n")
        _run_git(temp_repo, "add", "doc.md")
        _run_git(temp_repo, "commit", "-m", "add doc")

        blob = _blob_id(temp_repo, doc)
        create_comment(conn, file_id=blob, line_start=1, line_end=2,
                       author="hank", body="json test")

        results = collect_comments(
            file_path=str(doc), repo=str(temp_repo), db_path=str(db_path)
        )
        assert len(results) == 1
        c = results[0]
        # Verify expected fields exist
        for field in ("id", "author", "body", "resolved", "status",
                      "original_line_start", "original_line_end",
                      "current_line_start", "current_line_end",
                      "original_commit", "file_id"):
            assert field in c, f"Missing field: {field}"
