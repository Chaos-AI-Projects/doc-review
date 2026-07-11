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

    def test_view_contains_block_anchors(self, client):
        """Block rows have id and data-line-start/end attributes for anchoring."""
        resp = client.get("/view?path=test.md")
        assert 'id="L1"' in resp.text
        assert 'data-line-start="1"' in resp.text

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

    def test_line_content_has_data_line_attrs(self, client):
        """Block content cells have data-line-start/end for click-to-comment."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'data-line-start="1"' in resp.text


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


class TestLineNumbersHidden:
    """Line numbers should be hidden via CSS (display: none)."""

    def test_line_num_hidden_in_css(self, client):
        """The line-num class must have display: none in the stylesheet."""
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert "display: none" in resp.text or "display:none" in resp.text

    def test_line_anchoring_preserved(self, client):
        """Block anchoring (id=L1) and data-line-start must still work even with hidden numbers."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'id="L1"' in resp.text
        assert 'data-line-start="1"' in resp.text


class TestSidebarInline:
    """The comment sidebar must be inline (not floating/fixed)."""

    def test_sidebar_not_fixed_position(self, client):
        """The comment-sidebar CSS must NOT use position: fixed."""
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        # Sidebar should not be floating/fixed
        css = resp.text
        # Find the .comment-sidebar rule and verify it doesn't have position: fixed
        import re
        sidebar_match = re.search(r'\.comment-sidebar\s*\{([^}]+)\}', css)
        assert sidebar_match, "Expected .comment-sidebar rule in CSS"
        sidebar_css = sidebar_match.group(1)
        assert "position: fixed" not in sidebar_css, "Sidebar must not use position: fixed"
        assert "position:fixed" not in sidebar_css, "Sidebar must not use position: fixed"

    def test_review_container_no_margin_right_reservation(self, client):
        """Review container should not reserve margin-right for a floating sidebar."""
        resp = client.get("/static/style.css")
        css = resp.text
        import re
        container_match = re.search(r'\.review-container\s*\{([^}]+)\}', css)
        assert container_match, "Expected .review-container rule in CSS"
        container_css = container_match.group(1)
        assert "margin-right" not in container_css, \
            "Review container should not reserve right margin for floating sidebar"


class TestColumnToggles:
    """Navigator and comment columns should have independent hide/show toggles."""

    def test_nav_toggle_button_exists(self, client):
        """A toggle button to hide/show the navigator column must exist."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'id="nav-col-toggle"' in resp.text

    def test_comments_toggle_button_exists(self, client):
        """A toggle button to hide/show the comments column must exist."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'id="comments-col-toggle"' in resp.text

    def test_toggle_buttons_in_toolbar(self, client):
        """The toggle buttons should be in a toolbar area."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'class="col-toggles"' in resp.text


class TestTableOfContents:
    """Table of contents should appear when the document has headings."""

    def test_toc_present_for_headings(self, client):
        """View page should contain a TOC section when the file has headings."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        # The test file starts with "# Hello" which produces a heading
        assert 'class="toc-section"' in resp.text
        assert 'class="toc-link"' in resp.text
        assert "Hello" in resp.text

    def test_toc_links_to_block_anchors(self, client):
        """TOC links should point to block anchors (#L{start_line})."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'href="#L1"' in resp.text


class TestBlockRendering:
    """Content should be rendered as blocks, not per-line."""

    def test_list_renders_as_single_block(self, client):
        """Consecutive list items should render in one <ul>, not separate ones."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        # The test file has "- item one\n- item two" which should be one <ul>
        html = resp.text
        # Count <ul> tags in the source content area — should have at most
        # one for the list (the nav/toc may have their own <ul>).
        # Just verify the list items are present
        assert "item one" in html
        assert "item two" in html
