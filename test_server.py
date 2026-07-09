"""Route-level tests for the doc-review FastAPI server."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app, configure


@pytest.fixture
def source_dir():
    """Create a temp directory with a test markdown file."""
    with tempfile.TemporaryDirectory() as td:
        md_file = Path(td) / "test.md"
        md_file.write_text("# Hello\n\nThis is a test file.\n\n- item one\n- item two\n")
        sub = Path(td) / "sub"
        sub.mkdir()
        (sub / "nested.md").write_text("Nested content.\n")
        yield td


@pytest.fixture
def client(source_dir):
    db_path = Path(source_dir) / "test_comments.db"
    configure(source_dir, db_path)
    return TestClient(app)


class TestIndex:
    def test_index_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_index_lists_files(self, client):
        resp = client.get("/")
        assert "test.md" in resp.text
        assert "sub/nested.md" in resp.text


class TestViewFile:
    def test_view_existing_file(self, client):
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert "Hello" in resp.text
        assert "This is a test file." in resp.text

    def test_view_nested_file(self, client):
        resp = client.get("/view?path=sub/nested.md")
        assert resp.status_code == 200
        assert "Nested content." in resp.text

    def test_view_nonexistent_file(self, client):
        resp = client.get("/view?path=does_not_exist.md")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client):
        resp = client.get("/view?path=../../../etc/passwd")
        assert resp.status_code in (403, 404)

    def test_view_contains_line_anchors(self, client):
        """Line rows still have id and data-line attributes for anchoring."""
        resp = client.get("/view?path=test.md")
        assert 'id="L1"' in resp.text
        assert 'data-line="1"' in resp.text

    def test_view_contains_file_navigator(self, client):
        """View page includes a file-nav list with all files."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'class="file-nav"' in resp.text
        # All files should be listed in the navigator
        assert "test.md" in resp.text
        assert "sub/nested.md" in resp.text

    def test_file_navigator_provides_current_path(self, client):
        """View page includes current path data for JS to highlight active file."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert '"test.md"' in resp.text
        assert 'id="current-path-data"' in resp.text

    def test_file_navigator_present_for_nested_file(self, client):
        """File navigator works when viewing a nested file."""
        resp = client.get("/view?path=sub/nested.md")
        assert resp.status_code == 200
        assert 'class="file-nav"' in resp.text
        # Both files listed, nested one is active
        assert "test.md" in resp.text
        assert "sub/nested.md" in resp.text

    def test_line_content_has_data_line_attr(self, client):
        """Line content cells have data-line for click-to-comment."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'class="line-content" data-line="1"' in resp.text


class TestCommentFlow:
    def test_add_comment_redirects(self, client, source_dir):
        # Derive file_id first by viewing the file
        view_resp = client.get("/view?path=test.md")
        assert view_resp.status_code == 200

        resp = client.post(
            "/comment",
            data={
                "file_id": "testfakeid",
                "path": "test.md",
                "line_start": "1",
                "line_end": "1",
                "author": "tester",
                "body": "Nice heading!",
                "parent_id": "0",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/view?path=test.md" in resp.headers["location"]

    def test_comment_appears_after_posting(self, client, source_dir):
        # First derive the actual file_id by reading the view page
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        # Post a comment with the real file_id
        client.post(
            "/comment",
            data={
                "file_id": fid,
                "path": "test.md",
                "line_start": "1",
                "line_end": "1",
                "author": "reviewer",
                "body": "Check this line",
                "parent_id": "0",
            },
            follow_redirects=False,
        )
        # View — the comment data should be embedded as JSON
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        # The comment body should appear in the embedded JSON
        assert "Check this line" in resp.text

    def test_resolve_comment(self, client, source_dir):
        # Create comment
        from db import create_comment, get_connection, init_db

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)
        c = create_comment(
            conn,
            file_id="testfakeid",
            line_start=1,
            line_end=1,
            author="tester",
            body="resolve me",
        )
        conn.close()

        resp = client.post(
            f"/comment/{c['id']}/resolve",
            data={"path": "test.md"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_unresolve_comment(self, client, source_dir):
        from db import create_comment, get_connection, init_db, resolve_comment

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)
        c = create_comment(
            conn,
            file_id="testfakeid",
            line_start=1,
            line_end=1,
            author="tester",
            body="toggle me",
        )
        resolve_comment(conn, c["id"])
        conn.close()

        resp = client.post(
            f"/comment/{c['id']}/unresolve",
            data={"path": "test.md"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    def test_add_comment_without_author(self, client, source_dir):
        """Posting a comment without an author field should succeed with a default."""
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        resp = client.post(
            "/comment",
            data={
                "file_id": fid,
                "path": "test.md",
                "line_start": "1",
                "line_end": "1",
                "body": "No author comment",
                "parent_id": "0",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify the comment was stored with a default author
        from db import get_connection, list_comments

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        comments = list_comments(conn, fid)
        conn.close()
        matching = [c for c in comments if c["body"] == "No author comment"]
        assert len(matching) == 1
        assert matching[0]["author"] == "anon"

    def test_view_no_author_field_in_comment_form(self, client):
        """The comment form template should not contain an author input."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'name="author"' not in resp.text
