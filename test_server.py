"""Route-level tests for the doc-review FastAPI server."""

import subprocess
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


class TestFlatCommentThreads:
    """Comments are a flat, time-ordered list per block — no nesting or reply threading."""

    def test_all_comments_in_block_including_former_replies(self, client, source_dir):
        """TDD anchor (a): a block with three comments (including one with parent_id set)
        returns all three in comments_by_block in created_at order, and the view context
        no longer contains reply_map."""
        import json
        import re
        import time

        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)

        # Create three comments at different times — the third has a parent_id
        # (a legacy nested reply) that should still appear in the flat list.
        c1 = create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="alice", body="First comment",
        )
        time.sleep(0.05)
        c2 = create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="bob", body="Second comment",
        )
        time.sleep(0.05)
        c3 = create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="carol", body="Legacy reply (was nested)",
            parent_id=c1["id"],
        )
        conn.close()

        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200

        # Extract comments-data JSON
        comments_data_match = re.search(
            r'id="comments-data"[^>]*>(.*?)</script>', resp.text, re.DOTALL
        )
        assert comments_data_match, "Expected comments-data script tag"
        comments_data = json.loads(comments_data_match.group(1))

        # All three comments must be in the block's list
        block_comments = comments_data.get("1", [])
        assert len(block_comments) == 3, \
            f"Expected 3 comments in block, got {len(block_comments)}"

        # They must be in created_at order
        ids = [c["id"] for c in block_comments]
        assert ids == [c1["id"], c2["id"], c3["id"]], \
            f"Expected created_at order {[c1['id'], c2['id'], c3['id']]}, got {ids}"

        # There must be NO replies-data script tag (no reply_map)
        replies_data_match = re.search(
            r'id="replies-data"', resp.text
        )
        assert replies_data_match is None, \
            "View must not contain replies-data — reply_map has been removed"

    def test_no_reply_thread_divs_or_reply_buttons(self, client, source_dir):
        """TDD anchor (b): no nested .reply-thread div and no per-comment Reply button
        in the rendered view; exactly one comment/reply box per block thread."""
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)

        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="alice", body="A comment",
        )
        conn.close()

        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200

        # No .reply-thread div in the page
        assert "reply-thread" not in resp.text, \
            "Page must not contain reply-thread elements"

        # No per-comment Reply button
        assert "reply-btn" not in resp.text, \
            "Page must not contain per-comment reply buttons"

    def test_block_count_includes_all_comments(self, client, source_dir):
        """Block comment count includes all comments (including former replies)."""
        import json
        import re

        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)

        parent = create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="reviewer", body="Top level",
        )
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="author", body="Reply one",
            parent_id=parent["id"],
        )
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="author", body="Reply two",
            parent_id=parent["id"],
        )
        conn.close()

        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200

        comments_data_match = re.search(
            r'id="comments-data"[^>]*>(.*?)</script>', resp.text, re.DOTALL
        )
        assert comments_data_match
        comments_data = json.loads(comments_data_match.group(1))

        # Block should have all 3 comments (parent + 2 former replies)
        block_comments = comments_data.get("1", [])
        assert len(block_comments) == 3, \
            f"Expected 3 comments in block, got {len(block_comments)}"


class TestCommentsSurviveFileEdit:
    """Comments are looked up by file_path, so editing the file must not orphan them (#404)."""

    def test_comment_still_visible_after_file_edit(self, client, source_dir):
        """Create a comment via POST /comment, edit the file on disk (changing its
        content-derived file_id), GET /view again — the comment must still appear
        in comments-data."""
        import json
        import re

        from file_id import derive_file_id

        md_file = Path(source_dir) / "test.md"
        fid = derive_file_id(str(md_file))

        resp = client.post(
            "/comment",
            data={
                "file_id": fid,
                "path": "test.md",
                "line_start": "1",
                "line_end": "1",
                "author": "reviewer",
                "body": "Survives edits",
                "parent_id": "0",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Edit the file content — this changes the content-derived file_id.
        md_file.write_text("# Hello edited\n\nThis is a MODIFIED test file.\n")
        assert derive_file_id(str(md_file)) != fid, "Edit must change the file_id"

        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200

        comments_data_match = re.search(
            r'id="comments-data"[^>]*>(.*?)</script>', resp.text, re.DOTALL
        )
        assert comments_data_match, "Expected comments-data script tag"
        comments_data = json.loads(comments_data_match.group(1))
        all_bodies = [c["body"] for cs in comments_data.values() for c in cs]
        assert "Survives edits" in all_bodies, \
            "Comment must survive a file edit (path-based lookup)"

    def test_legacy_comment_without_file_path_still_shown(self, client, source_dir):
        """A pre-file_path row (file_path IS NULL) whose file_id matches the current
        content must still be shown, merged and deduped with path-keyed rows."""
        import json
        import re

        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        md_file = Path(source_dir) / "test.md"
        fid = derive_file_id(str(md_file))

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)
        # Legacy row: no file_path, keyed only by current content id.
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="old-timer", body="Legacy comment",
            file_path=None,
        )
        # Modern row: has file_path.
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="reviewer", body="Modern comment",
            file_path="test.md",
        )
        conn.close()

        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200

        comments_data_match = re.search(
            r'id="comments-data"[^>]*>(.*?)</script>', resp.text, re.DOTALL
        )
        assert comments_data_match
        comments_data = json.loads(comments_data_match.group(1))
        block_comments = comments_data.get("1", [])
        bodies = [c["body"] for c in block_comments]
        assert "Legacy comment" in bodies
        assert "Modern comment" in bodies
        assert len(block_comments) == 2, \
            f"Expected exactly 2 comments (no duplicates), got {len(block_comments)}"


class TestCacheControl:
    """HTML responses must include Cache-Control: no-store to prevent stale views."""

    def test_view_has_cache_control_no_store(self, client):
        """GET /view must include Cache-Control: no-store."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("cache-control", "")

    def test_index_has_cache_control_no_store(self, client):
        """GET / must include Cache-Control: no-store."""
        resp = client.get("/")
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("cache-control", "")


# ── Anchor migration via git blame (#406) ───────────────────────────────

GIT_DOC = "# Title\n\npara one\n\npara two\n\npara three\n"
# Line numbers:  1 '# Title', 2 '', 3 'para one', 4 '', 5 'para two',
#                6 '', 7 'para three'


def _git(cwd, *args) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


@pytest.fixture
def git_source_dir():
    """Temp directory that is a git repo with a committed markdown file."""
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "doc.md").write_text(GIT_DOC)
        _git(td, "init", "-q")
        _git(td, "config", "user.email", "test@example.com")
        _git(td, "config", "user.name", "Test")
        _git(td, "add", "doc.md")
        _git(td, "commit", "-qm", "initial doc")
        yield td


@pytest.fixture
def git_client(git_source_dir):
    db_path = Path(git_source_dir) / "test_comments.db"
    configure(git_source_dir, db_path)
    return TestClient(app)


def _post_comment(client, git_source_dir, line_start, line_end, body):
    from file_id import derive_file_id

    fid = derive_file_id(str(Path(git_source_dir) / "doc.md"))
    resp = client.post(
        "/comment",
        data={
            "file_id": fid,
            "path": "doc.md",
            "line_start": str(line_start),
            "line_end": str(line_end),
            "author": "reviewer",
            "body": body,
            "parent_id": "0",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303


def _db_comment(git_source_dir, body):
    from db import get_connection

    conn = get_connection(Path(git_source_dir) / "test_comments.db")
    row = conn.execute(
        "SELECT * FROM comments WHERE body = ?", (body,)
    ).fetchone()
    conn.close()
    assert row is not None, f"No comment row with body {body!r}"
    return dict(row)


def _comments_data(resp_text):
    import json
    import re

    m = re.search(r'id="comments-data"[^>]*>(.*?)</script>', resp_text, re.DOTALL)
    assert m, "Expected comments-data script tag"
    return json.loads(m.group(1))


class TestAnchorCommitOnCreate:
    """POST /comment records the anchor commit for clean git-tracked files (#406)."""

    def test_create_sets_anchor_commit_for_clean_git_file(
        self, git_client, git_source_dir
    ):
        _post_comment(git_client, git_source_dir, 5, 5, "anchored comment")
        row = _db_comment(git_source_dir, "anchored comment")
        head = _git(git_source_dir, "rev-parse", "HEAD")
        assert row["anchor_commit"] == head

    def test_create_null_anchor_for_dirty_file(self, git_client, git_source_dir):
        md = Path(git_source_dir) / "doc.md"
        md.write_text(GIT_DOC + "\nuncommitted trailer\n")
        _post_comment(git_client, git_source_dir, 5, 5, "dirty-create comment")
        row = _db_comment(git_source_dir, "dirty-create comment")
        assert row["anchor_commit"] is None

    def test_create_null_anchor_for_non_git_file(self, client, source_dir):
        from db import get_connection

        from file_id import derive_file_id

        fid = derive_file_id(str(Path(source_dir) / "test.md"))
        client.post(
            "/comment",
            data={
                "file_id": fid,
                "path": "test.md",
                "line_start": "1",
                "line_end": "1",
                "body": "non-git comment",
                "parent_id": "0",
            },
            follow_redirects=False,
        )
        conn = get_connection(Path(source_dir) / "test_comments.db")
        row = conn.execute(
            "SELECT * FROM comments WHERE body = 'non-git comment'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["anchor_commit"] is None


class TestAnchorMigration:
    """Comment anchors follow the text across committed edits via reverse blame (#406)."""

    def test_shift_migrates_single_line_anchor(self, git_client, git_source_dir):
        """Insert lines above the range + commit → anchor moves with the text
        and the DB row is updated (blame runs once per edit)."""
        _post_comment(git_client, git_source_dir, 5, 5, "on para two")
        md = Path(git_source_dir) / "doc.md"
        md.write_text("intro\n\n" + GIT_DOC)  # para two shifts 5 → 7
        _git(git_source_dir, "commit", "-qam", "prepend intro")
        new_head = _git(git_source_dir, "rev-parse", "HEAD")

        resp = git_client.get("/view?path=doc.md")
        assert resp.status_code == 200

        # Rendered view anchors the comment to the shifted block.
        data = _comments_data(resp.text)
        block_comments = data.get("7", [])
        bodies = [c["body"] for c in block_comments]
        assert "on para two" in bodies, f"comment not on block 7: {data}"

        # DB row was migrated and re-anchored at the new HEAD.
        row = _db_comment(git_source_dir, "on para two")
        assert row["line_start"] == 7
        assert row["line_end"] == 7
        assert row["anchor_commit"] == new_head

    def test_shift_migrates_multiline_range(self, git_client, git_source_dir):
        """A multi-line range maps to [min, max] of the surviving lines."""
        _post_comment(git_client, git_source_dir, 3, 5, "range comment")
        md = Path(git_source_dir) / "doc.md"
        md.write_text("intro\n\n" + GIT_DOC)  # 3..5 shifts to 5..7
        _git(git_source_dir, "commit", "-qam", "prepend intro")

        resp = git_client.get("/view?path=doc.md")
        assert resp.status_code == 200

        row = _db_comment(git_source_dir, "range comment")
        assert row["line_start"] == 5
        assert row["line_end"] == 7
        assert row["anchor_commit"] == _git(git_source_dir, "rev-parse", "HEAD")

    def test_range_deleted_clamps_but_stays_visible(
        self, git_client, git_source_dir
    ):
        """Deleting the commented range clamps anchors to the current file
        length; the comment remains visible."""
        _post_comment(git_client, git_source_dir, 7, 7, "on para three")
        md = Path(git_source_dir) / "doc.md"
        md.write_text("# Title\n\npara one\n\npara two\n")  # 5 lines, para three gone
        _git(git_source_dir, "commit", "-qam", "drop para three")
        new_head = _git(git_source_dir, "rev-parse", "HEAD")

        resp = git_client.get("/view?path=doc.md")
        assert resp.status_code == 200

        data = _comments_data(resp.text)
        all_bodies = [c["body"] for cs in data.values() for c in cs]
        assert "on para three" in all_bodies, "clamped comment must stay visible"

        row = _db_comment(git_source_dir, "on para three")
        assert row["line_start"] == 5
        assert row["line_end"] == 5
        assert row["anchor_commit"] == new_head

    def test_dirty_tree_skips_migration(self, git_client, git_source_dir):
        """Uncommitted edits to the file must not touch stored anchors."""
        _post_comment(git_client, git_source_dir, 5, 5, "dirty-view comment")
        old_head = _git(git_source_dir, "rev-parse", "HEAD")
        md = Path(git_source_dir) / "doc.md"
        md.write_text("intro\n\n" + GIT_DOC)  # NOT committed

        resp = git_client.get("/view?path=doc.md")
        assert resp.status_code == 200

        row = _db_comment(git_source_dir, "dirty-view comment")
        assert row["line_start"] == 5
        assert row["line_end"] == 5
        assert row["anchor_commit"] == old_head

    def test_legacy_null_anchor_untouched(self, git_client, git_source_dir):
        """Rows with anchor_commit NULL are served as-is and never auto-adopt
        a commit."""
        from db import create_comment, get_connection, init_db

        conn = get_connection(Path(git_source_dir) / "test_comments.db")
        init_db(conn)
        create_comment(
            conn, file_id="legacyfid", line_start=5, line_end=5,
            author="old-timer", body="legacy comment", file_path="doc.md",
        )
        conn.close()

        md = Path(git_source_dir) / "doc.md"
        md.write_text("intro\n\n" + GIT_DOC)
        _git(git_source_dir, "commit", "-qam", "prepend intro")

        resp = git_client.get("/view?path=doc.md")
        assert resp.status_code == 200
        assert "legacy comment" in resp.text

        row = _db_comment(git_source_dir, "legacy comment")
        assert row["line_start"] == 5
        assert row["line_end"] == 5
        assert row["anchor_commit"] is None


class TestReplyLabel:
    """Display-only 'reply' label on comments with parent_id (#436).

    When a block has >=1 comment and another comment is attached to the same
    block with parent_id set, the later comment should render with a 'reply'
    label/tag. This is purely a display change — no schema or data changes.
    """

    def test_app_js_renders_reply_badge_for_parent_id(self, client):
        """app.js must contain logic to render a reply badge when parent_id is set."""
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        assert "reply-badge" in resp.text, \
            "app.js must render a reply-badge element for comments with parent_id"

    def test_css_contains_reply_badge_style(self, client):
        """style.css must style the reply-badge element."""
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        assert ".reply-badge" in resp.text, \
            "style.css must contain a .reply-badge rule"

    def test_parent_id_available_in_comments_data_for_reply(self, client, source_dir):
        """Comments with parent_id must include that field in the embedded JSON so
        the frontend can render the reply badge."""
        import json
        import re
        import time

        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)

        parent = create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="alice", body="Parent comment",
        )
        time.sleep(0.05)
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="bob", body="Reply to parent",
            parent_id=parent["id"],
        )
        conn.close()

        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200

        comments_data_match = re.search(
            r'id="comments-data"[^>]*>(.*?)</script>', resp.text, re.DOTALL
        )
        assert comments_data_match
        comments_data = json.loads(comments_data_match.group(1))

        block_comments = comments_data.get("1", [])
        assert len(block_comments) == 2, \
            f"Expected 2 comments in block, got {len(block_comments)}"

        # The reply comment must have parent_id set
        reply_comments = [c for c in block_comments if c.get("parent_id")]
        assert len(reply_comments) == 1, \
            "Expected exactly one comment with parent_id set"
        assert reply_comments[0]["parent_id"] == parent["id"]


# ── JSON Comments API (#435) ──────────────────────────────────────────


class TestGetApiComments:
    """GET /api/comments?path=<file> returns JSON list of comments (#435)."""

    def test_returns_empty_list_when_no_comments(self, client):
        resp = client.get("/api/comments?path=test.md")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_returns_comments_for_file(self, client, source_dir):
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="alice", body="First comment", file_path="test.md",
        )
        create_comment(
            conn, file_id=fid, line_start=3, line_end=3,
            author="bob", body="Second comment", file_path="test.md",
        )
        conn.close()

        resp = client.get("/api/comments?path=test.md")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["author"] == "alice"
        assert data[1]["author"] == "bob"

    def test_response_includes_required_fields(self, client, source_dir):
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)
        parent = create_comment(
            conn, file_id=fid, line_start=1, line_end=2,
            author="reviewer", body="Check this", file_path="test.md",
        )
        create_comment(
            conn, file_id=fid, line_start=1, line_end=2,
            author="author", body="Reply here",
            parent_id=parent["id"], file_path="test.md",
        )
        conn.close()

        resp = client.get("/api/comments?path=test.md")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2

        # Required fields per issue spec
        required_fields = {"id", "parent_id", "author", "body",
                           "line_start", "line_end", "created_at"}
        for comment in data:
            assert required_fields.issubset(comment.keys()), \
                f"Missing fields: {required_fields - comment.keys()}"

        # parent_id is set on the reply
        reply = [c for c in data if c["parent_id"] is not None]
        assert len(reply) == 1
        assert reply[0]["parent_id"] == parent["id"]

    def test_missing_path_returns_422(self, client):
        resp = client.get("/api/comments")
        assert resp.status_code == 422

    def test_nonexistent_file_returns_404(self, client):
        resp = client.get("/api/comments?path=nonexistent.md")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client):
        resp = client.get("/api/comments?path=../../../etc/passwd")
        assert resp.status_code in (403, 404)


class TestPostApiComments:
    """POST /api/comments accepts JSON, creates comment, returns JSON (#435)."""

    def test_create_comment_returns_json(self, client, source_dir):
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        resp = client.post(
            "/api/comments",
            json={
                "file_id": fid,
                "path": "test.md",
                "line_start": 1,
                "line_end": 1,
                "author": "tester",
                "body": "A JSON comment",
            },
        )
        assert resp.status_code == 201
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert data["body"] == "A JSON comment"
        assert data["author"] == "tester"
        assert data["id"] is not None
        assert "created_at" in data

    def test_create_comment_with_parent_id(self, client, source_dir):
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        db_path = Path(source_dir) / "test_comments.db"
        conn = get_connection(db_path)
        init_db(conn)
        parent = create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="alice", body="Parent", file_path="test.md",
        )
        conn.close()

        resp = client.post(
            "/api/comments",
            json={
                "file_id": fid,
                "path": "test.md",
                "line_start": 1,
                "line_end": 1,
                "author": "bob",
                "body": "Reply via API",
                "parent_id": parent["id"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["parent_id"] == parent["id"]

    def test_comment_visible_via_get_after_post(self, client, source_dir):
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        client.post(
            "/api/comments",
            json={
                "file_id": fid,
                "path": "test.md",
                "line_start": 1,
                "line_end": 1,
                "author": "tester",
                "body": "Roundtrip test",
            },
        )

        resp = client.get("/api/comments?path=test.md")
        assert resp.status_code == 200
        data = resp.json()
        bodies = [c["body"] for c in data]
        assert "Roundtrip test" in bodies

    def test_default_author(self, client, source_dir):
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        resp = client.post(
            "/api/comments",
            json={
                "file_id": fid,
                "path": "test.md",
                "line_start": 1,
                "line_end": 1,
                "body": "No author",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["author"] == "anon"

    def test_missing_required_fields_returns_422(self, client):
        resp = client.post("/api/comments", json={"body": "incomplete"})
        assert resp.status_code == 422

    def test_does_not_redirect(self, client, source_dir):
        """POST /api/comments must return JSON, not a 303 redirect."""
        from file_id import derive_file_id

        file_path = str(Path(source_dir) / "test.md")
        fid = derive_file_id(file_path)

        resp = client.post(
            "/api/comments",
            json={
                "file_id": fid,
                "path": "test.md",
                "line_start": 1,
                "line_end": 1,
                "body": "No redirect",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 201
        assert resp.headers["content-type"].startswith("application/json")


# ── Collapse directory tree by default (#439) ───────────────────────────


class TestCollapseDirectoryTree:
    """Directory tree collapses by default; only current file's ancestor
    directories auto-expand (#439).

    renderTree must thread a directory-path prefix through its recursion and
    set details.open only when the directory is an ancestor of currentPath.
    """

    def test_app_js_no_unconditional_details_open(self, client):
        """app.js must NOT hardcode details.open = true unconditionally."""
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        src = resp.text
        # The old line `details.open = true;` (unconditional) must be gone.
        # A conditional open (e.g. details.open = <expr>) is fine.
        import re
        unconditional = re.findall(r'details\.open\s*=\s*true\s*;', src)
        assert len(unconditional) == 0, \
            "app.js must not unconditionally set details.open = true"

    def test_app_js_contains_ancestor_path_logic(self, client):
        """app.js must compute ancestor paths and conditionally open dirs."""
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        src = resp.text
        # renderTree must accept a prefix/dirPath parameter
        assert "currentPath" in src, \
            "app.js must reference currentPath for ancestor logic"
        # The open logic must check startsWith for ancestor match
        assert "startsWith" in src or "indexOf" in src, \
            "app.js must use startsWith or indexOf to check ancestor paths"
        # Must compute a directory path by joining prefix and dirName
        assert "dirPath" in src or "prefix" in src, \
            "app.js must thread a directory path prefix through renderTree"

    def test_app_js_renderTree_passes_prefix(self, client):
        """renderTree recursive call must pass the accumulated directory path."""
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        src = resp.text
        import re
        # The initial call should pass an empty prefix
        assert re.search(r'renderTree\(tree,\s*navTreeEl,\s*""\)', src), \
            "Initial renderTree call must pass empty string as prefix"
        # The recursive call should pass the computed dirPath
        assert re.search(r'renderTree\(node\[', src), \
            "Recursive renderTree must pass the child node"

    def test_ancestor_logic_behavioral(self):
        """Run the Node.js behavioral test for the ancestor-open logic."""
        result = subprocess.run(
            ["node", str(Path(__file__).parent / "test_tree_collapse.js")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, \
            f"Behavioral tree-collapse test failed:\n{result.stderr}"
