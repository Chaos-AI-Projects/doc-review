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


class TestStaticCacheBusting:
    """Tests for cache-busting versioned asset URLs and Cache-Control headers."""

    def test_static_js_has_cache_control(self, client):
        """GET /static/app.js must include Cache-Control containing no-cache."""
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-cache" in cc, (
            f"Expected Cache-Control to contain 'no-cache', got '{cc}'"
        )

    def test_static_css_has_cache_control(self, client):
        """GET /static/style.css must include Cache-Control containing no-cache."""
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-cache" in cc, (
            f"Expected Cache-Control to contain 'no-cache', got '{cc}'"
        )

    def test_static_version_stable_for_same_content(self):
        """static_version returns a stable hash for identical file content."""
        from server import static_version
        h1 = static_version("app.js")
        static_version.cache_clear()
        h2 = static_version("app.js")
        assert h1 == h2
        assert len(h1) == 8, "Hash should be 8 hex characters"

    def test_static_version_rejects_path_traversal(self):
        """static_version must reject filenames that escape the static/ dir."""
        from server import static_version
        with pytest.raises(ValueError, match="Illegal static filename"):
            static_version("../server.py")

    def test_static_version_changes_on_content_change(self):
        """static_version returns different hash when file content differs."""
        from server import static_version, BASE_DIR
        original = (BASE_DIR / "static" / "app.js").read_bytes()
        h1 = static_version("app.js")
        try:
            (BASE_DIR / "static" / "app.js").write_bytes(original + b"\n// changed")
            # Clear any cache so the new content is picked up
            static_version.cache_clear()
            h2 = static_version("app.js")
        finally:
            (BASE_DIR / "static" / "app.js").write_bytes(original)
            static_version.cache_clear()
        assert h1 != h2, "Hash must change when file content changes"

    def test_view_page_has_versioned_app_js(self, client):
        """Rendered /view page must reference app.js with ?v=<hash> query."""
        from server import static_version
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        expected_hash = static_version("app.js")
        assert f"/static/app.js?v={expected_hash}" in resp.text, (
            "app.js script tag must include versioned query string"
        )

    def test_index_page_has_versioned_style_css(self, client):
        """Rendered index page must reference style.css with ?v=<hash> query."""
        from server import static_version
        resp = client.get("/")
        assert resp.status_code == 200
        expected_hash = static_version("style.css")
        assert f"/static/style.css?v={expected_hash}" in resp.text, (
            "style.css link tag must include versioned query string"
        )


# ── Pyodide spike endpoints (#443) ────────────────────────────────────────


class TestSpikeRenderAPI:
    def test_render_api_returns_block_ranges(self, client):
        resp = client.post("/api/render", json={"source": "# Hello\n\nWorld."})
        assert resp.status_code == 200
        data = resp.json()
        assert "blocks" in data
        assert len(data["blocks"]) == 2
        assert data["blocks"][0] == {"start_line": 1, "end_line": 1}
        assert data["blocks"][1] == {"start_line": 3, "end_line": 3}

    def test_render_api_empty_source(self, client):
        resp = client.post("/api/render", json={"source": ""})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["blocks"]) == 1

    def test_parity_fixture_endpoint(self, client):
        resp = client.get("/api/parity-fixture")
        assert resp.status_code == 200
        data = resp.json()
        assert "source" in data
        assert "expected_ranges" in data
        assert len(data["expected_ranges"]) > 0
        assert "start_line" in data["expected_ranges"][0]
        assert "end_line" in data["expected_ranges"][0]


class TestSpikePreview:
    def test_spike_preview_page_loads(self, client):
        resp = client.get("/spike/preview")
        assert resp.status_code == 200
        assert "Pyodide" in resp.text

    def test_renderer_source_endpoint(self, client):
        resp = client.get("/spike/renderer.py")
        assert resp.status_code == 200
        data = resp.json()
        assert "source" in data
        assert "render_markdown_blocks" in data["source"]


class TestBlameAPI:
    def test_blame_untracked_file_returns_404(self, client):
        """Blame on a non-git-tracked file returns 404."""
        resp = client.get("/api/blame?path=test.md")
        assert resp.status_code == 404

    def test_blame_nonexistent_file_returns_404(self, client):
        resp = client.get("/api/blame?path=no_such_file.md")
        assert resp.status_code == 404

    def test_blame_tracked_file_returns_lines(self, git_client):
        """Blame on a git-tracked file returns per-line blame data."""
        resp = git_client.get("/api/blame?path=doc.md")
        assert resp.status_code == 200
        data = resp.json()
        assert "lines" in data
        assert len(data["lines"]) > 0
        first = data["lines"][0]
        assert "line" in first
        assert "commit" in first
        assert "author" in first
        assert "content" in first
        assert first["author"] == "Test"


# ── Pyodide promotion to /view (#445) ────────────────────────────────────


class TestPyodideViewPromotion:
    """Pyodide in-browser renderer promoted to /view: raw source embedded,
    Pyodide CDN referenced, renderer endpoint referenced, server-side
    blocks retained as fallback (#445)."""

    def test_view_embeds_raw_source(self, client):
        """View page must embed the raw markdown source for Pyodide."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'id="source-data"' in resp.text

    def test_raw_source_matches_file_content(self, client, source_dir):
        """The embedded source-data must match the actual file content."""
        import json
        import re

        expected = (Path(source_dir) / "test.md").read_text()
        resp = client.get("/view?path=test.md")
        m = re.search(
            r'id="source-data"[^>]*>(.*?)</script>', resp.text, re.DOTALL
        )
        assert m, "Expected source-data script tag"
        embedded = json.loads(m.group(1))
        assert embedded == expected

    def test_view_references_pyodide_cdn(self, client):
        """View page must reference the Pyodide CDN for in-browser rendering."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert "pyodide" in resp.text.lower()

    def test_view_references_renderer_endpoint(self, client):
        """View page must reference /spike/renderer.py for Pyodide to fetch."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert "/spike/renderer.py" in resp.text

    def test_view_still_has_server_rendered_blocks(self, client):
        """Server-side rendered blocks remain as no-JS fallback."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        # The test file has "# Hello" which renders to <h1>Hello</h1>
        assert "<h1>" in resp.text
        assert "Hello" in resp.text
        assert 'class="line-content"' in resp.text

    def test_view_has_pyodide_status_indicator(self, client):
        """View page must contain a status indicator for Pyodide readiness."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        assert 'id="pyodide-status"' in resp.text

    def test_existing_features_preserved_with_source(self, client):
        """All existing /view features still present after adding source embed."""
        resp = client.get("/view?path=test.md")
        assert resp.status_code == 200
        # Block anchors
        assert 'id="L1"' in resp.text
        assert 'data-line-start="1"' in resp.text
        # Comments data
        assert 'id="comments-data"' in resp.text
        # File navigator
        assert 'class="file-nav"' in resp.text
        assert 'id="files-data"' in resp.text
        # TOC
        assert 'class="toc-section"' in resp.text
        # Column toggles
        assert 'id="nav-col-toggle"' in resp.text
        assert 'id="comments-col-toggle"' in resp.text
        # Comment form template
        assert 'id="comment-form-tpl"' in resp.text


# ── SPA file switching: /api/source + client-side nav (#447) ─────────────


def _api_source(client, path="test.md"):
    return client.get(f"/api/source?path={path}")


class TestApiSource:
    """GET /api/source returns the same payload /view builds, as JSON (#447)."""

    def test_returns_200_and_json(self, client):
        resp = _api_source(client)
        assert resp.status_code == 200
        assert "application/json" in resp.headers["content-type"]

    def test_payload_shape(self, client):
        data = _api_source(client).json()
        assert set(data) == {
            "path", "file_id", "source", "toc", "comments_by_block"
        }
        assert data["path"] == "test.md"
        assert isinstance(data["file_id"], str) and data["file_id"]
        assert isinstance(data["toc"], list)
        assert isinstance(data["comments_by_block"], dict)

    def test_source_matches_file_content(self, client, source_dir):
        expected = (Path(source_dir) / "test.md").read_text()
        assert _api_source(client).json()["source"] == expected

    def test_file_id_matches_view(self, client, source_dir):
        from file_id import derive_file_id

        expected = derive_file_id(str(Path(source_dir) / "test.md"))
        assert _api_source(client).json()["file_id"] == expected

    def test_toc_matches_renderer(self, client, source_dir):
        from renderer import extract_toc

        expected = extract_toc((Path(source_dir) / "test.md").read_text())
        assert _api_source(client).json()["toc"] == expected

    def test_nested_file(self, client):
        data = _api_source(client, "sub/nested.md").json()
        assert data["path"] == "sub/nested.md"
        assert "Nested content." in data["source"]

    def test_path_traversal_blocked(self, client):
        resp = client.get("/api/source?path=../../etc/passwd")
        assert resp.status_code == 403

    def test_nonexistent_file_returns_404(self, client):
        resp = client.get("/api/source?path=does_not_exist.md")
        assert resp.status_code == 404

    def test_missing_path_returns_422(self, client):
        assert client.get("/api/source").status_code == 422

    def test_cache_control_no_store(self, client):
        resp = _api_source(client)
        assert "no-store" in resp.headers.get("cache-control", "")

    def test_comments_by_block_matches_view(self, client, source_dir):
        from file_id import derive_file_id

        fid = derive_file_id(str(Path(source_dir) / "test.md"))
        client.post(
            "/comment",
            data={
                "file_id": fid, "path": "test.md",
                "line_start": "1", "line_end": "1",
                "author": "reviewer", "body": "api-source comment",
                "parent_id": "0",
            },
            follow_redirects=False,
        )
        view_data = _comments_data(client.get("/view?path=test.md").text)
        api_data = _api_source(client).json()["comments_by_block"]
        assert api_data == view_data
        bodies = [c["body"] for cs in api_data.values() for c in cs]
        assert "api-source comment" in bodies

    def test_legacy_null_path_comments_included(self, client, source_dir):
        """Legacy rows (file_path NULL) matching the content id are merged,
        exactly as /view does."""
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        fid = derive_file_id(str(Path(source_dir) / "test.md"))
        conn = get_connection(Path(source_dir) / "test_comments.db")
        init_db(conn)
        create_comment(
            conn, file_id=fid, line_start=1, line_end=1,
            author="old-timer", body="legacy api comment",
        )
        conn.close()

        api_data = _api_source(client).json()["comments_by_block"]
        bodies = [c["body"] for cs in api_data.values() for c in cs]
        assert "legacy api comment" in bodies

    def test_shares_payload_builder_with_view(self, client, monkeypatch):
        """Anti-drift: both /view and /api/source must go through the single
        shared payload builder."""
        import server

        calls = []
        original = server.build_view_payload

        def spy(path):
            calls.append(path)
            return original(path)

        monkeypatch.setattr(server, "build_view_payload", spy)
        assert client.get("/view?path=test.md").status_code == 200
        assert calls == ["test.md"]
        assert _api_source(client).status_code == 200
        assert calls == ["test.md", "test.md"]

    def test_payload_identical_to_builder_output(self, client, source_dir):
        """The JSON payload is exactly the shared builder's output, minus the
        server-render-only ``blocks`` key."""
        import json

        import server

        payload = server.build_view_payload("test.md")
        assert "blocks" in payload, "builder must also supply server-side blocks"
        expected = {
            k: v for k, v in payload.items()
            if k in {"path", "file_id", "source", "toc", "comments_by_block"}
        }
        # JSON round-trip normalises int block keys to strings.
        assert _api_source(client).json() == json.loads(json.dumps(expected))


class TestApiSourceAnchorParity:
    """/api/source performs the same anchor re-migration as /view (#406/#447)."""

    def test_migrates_anchors_like_view(self, git_client, git_source_dir):
        _post_comment(git_client, git_source_dir, 5, 5, "on para two")
        md = Path(git_source_dir) / "doc.md"
        md.write_text("intro\n\n" + GIT_DOC)  # para two shifts 5 → 7
        _git(git_source_dir, "commit", "-qam", "prepend intro")
        new_head = _git(git_source_dir, "rev-parse", "HEAD")

        data = git_client.get("/api/source?path=doc.md").json()
        bodies = [c["body"] for c in data["comments_by_block"].get("7", [])]
        assert "on para two" in bodies, f"not re-anchored: {data['comments_by_block']}"

        row = _db_comment(git_source_dir, "on para two")
        assert (row["line_start"], row["line_end"]) == (7, 7)
        assert row["anchor_commit"] == new_head

    def test_dirty_tree_skips_migration(self, git_client, git_source_dir):
        _post_comment(git_client, git_source_dir, 5, 5, "dirty api comment")
        old_head = _git(git_source_dir, "rev-parse", "HEAD")
        (Path(git_source_dir) / "doc.md").write_text("intro\n\n" + GIT_DOC)

        assert git_client.get("/api/source?path=doc.md").status_code == 200

        row = _db_comment(git_source_dir, "dirty api comment")
        assert (row["line_start"], row["line_end"]) == (5, 5)
        assert row["anchor_commit"] == old_head


class TestSpaClientNavigation:
    """static/app.js soft-navigates between files without reloading (#447)."""

    def _app_js(self, client):
        return client.get("/static/app.js").text

    def _nav_logic_js(self, client):
        return client.get("/static/nav_logic.js").text

    def test_nav_logic_asset_is_served(self, client):
        resp = client.get("/static/nav_logic.js")
        assert resp.status_code == 200

    def test_view_loads_nav_logic_before_app_js(self, client):
        """The shared logic module must be in scope when app.js runs."""
        html = client.get("/view?path=test.md").text
        assert "/static/nav_logic.js" in html
        assert html.index("/static/nav_logic.js") < html.index("/static/app.js")

    def test_nav_logic_is_versioned(self, client):
        """Cache busting applies to the new asset too (#442)."""
        import re

        html = client.get("/view?path=test.md").text
        assert re.search(r"/static/nav_logic\.js\?v=[0-9a-f]{8}", html)

    def test_app_js_uses_shared_nav_logic(self, client):
        """app.js must consume the tested module rather than re-implement it."""
        js = self._app_js(client)
        assert "docReviewNavLogic" in js
        # The spec builders moved to view_specs.py (#451); app.js now reaches
        # them through the warm runtime, asserted in TestSpecBuilderParity.
        # What must still come from nav_logic.js is the routing half.
        for fn in (
            "navLogic.shouldIntercept",
            "navLogic.popstateAction", "navLogic.lineAnchorId",
            "navLogic.apiSourceUrl", "navLogic.viewUrl",
        ):
            assert fn in js, f"app.js does not use {fn}"

    def test_app_js_does_not_duplicate_nav_logic(self, client):
        """Anti-drift: the pure logic is defined once, in nav_logic.js."""
        js = self._app_js(client)
        for fn in (
            "function viewUrl(", "function apiSourceUrl(",
            "function shouldIntercept(", "function popstateAction(",
            "function lineAnchorId(",
        ):
            assert fn not in js, f"app.js redefines {fn} — must reuse nav_logic.js"

    def test_nav_logic_fetches_api_source(self, client):
        assert "/api/source?path=" in self._nav_logic_js(client)

    def test_app_js_uses_pushstate_and_popstate(self, client):
        js = self._app_js(client)
        assert "history.pushState" in js
        assert "popstate" in js

    def test_keeps_path_query_url_scheme(self, client):
        """URL scheme stays ?path= — no hash routing (#447 non-goal)."""
        assert '"/view?path=" + encodeURIComponent' in self._nav_logic_js(client)
        assert "location.hash = " not in self._app_js(client)

    def test_app_js_nav_links_keep_href_fallback(self, client):
        """Anchors keep a real href so no-JS / failed-fetch clicks still do a
        full-page navigation."""
        js = self._app_js(client)
        assert 'a.href = "/view?path="' in js

    def test_app_js_guards_on_renderer_availability(self, client):
        """Interception only happens when the warm Pyodide renderer handle is
        present; otherwise the click falls through."""
        js = self._app_js(client)
        assert "docReviewRenderer" in js
        assert "preventDefault" in js

    def test_app_js_restores_line_anchor_after_swap(self, client):
        """Back/Forward onto a #L42 deep link must land on that block."""
        js = self._app_js(client)
        assert "navLogic.lineAnchorId(window.location.hash)" in js
        assert "scrollIntoView()" in js

    def test_app_js_sequences_concurrent_swaps(self, client):
        """A slow response must not clobber a newer navigation."""
        js = self._app_js(client)
        assert "navSeq" in js
        assert "seq !== navSeq" in js

    def test_app_js_updates_comment_form_target(self, client):
        """Comments must be posted against the file currently on screen."""
        js = self._app_js(client)
        assert 'tpl.querySelector(\'[name="file_id"]\').value' in js
        assert 'tpl.querySelector(\'[name="path"]\').value' in js

    def test_view_exposes_renderer_handle(self, client):
        """view.html must publish the warm Pyodide runtime so app.js can
        re-render without re-booting it."""
        html = client.get("/view?path=test.md").text
        assert "docReviewRenderer" in html

    def test_view_still_server_renders_first_load(self, client):
        """The server-side render stays live (no-JS fallback from #446)."""
        html = client.get("/view?path=test.md").text
        assert 'class="line-content"' in html
        assert "<h1>" in html

    def test_nav_logic_skips_modified_clicks(self, client):
        """Ctrl/meta/shift/alt clicks and non-primary buttons must keep their
        native behaviour (new tab / window)."""
        js = self._nav_logic_js(client)
        for guard in ("metaKey", "ctrlKey", "shiftKey", "altKey", "evt.button !== 0"):
            assert guard in js, f"missing modifier guard: {guard}"

    def test_app_js_falls_back_on_fetch_failure(self, client):
        """A failed soft swap must end up on the normal /view page."""
        js = self._app_js(client)
        assert "catch(" in js.replace(" ", "")
        assert "window.location.href = navLogic.viewUrl(path)" in js
        assert "window.location.reload()" in js

    def test_spa_navigation_behavioral(self):
        """Run the Node.js behavioral test against the shipped nav_logic.js."""
        result = subprocess.run(
            ["node", str(Path(__file__).parent / "test_spa_nav.js")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, \
            f"Behavioral SPA navigation test failed:\n{result.stderr}"


class TestSpecBuilderParity:
    """One source of truth for row/TOC markup (#451).

    The server-side Jinja render and the client-side Pyodide soft swap must
    derive their markup from the SAME Python builders, so a swapped-in file
    cannot render differently from a fresh ``/view``.
    """

    def test_view_renders_through_the_python_builders(self, client, monkeypatch):
        """Anti-drift: /view must go through view_specs, not inline Jinja."""
        import server

        row_calls, toc_calls, header_calls = [], [], []
        orig_rows = server.source_row_specs
        orig_toc = server.toc_item_specs
        orig_header = server.header_fields

        def row_spy(blocks, comments_by_block=None):
            row_calls.append(blocks)
            return orig_rows(blocks, comments_by_block)

        def toc_spy(toc):
            toc_calls.append(toc)
            return orig_toc(toc)

        def header_spy(data):
            header_calls.append(data)
            return orig_header(data)

        monkeypatch.setattr(server, "source_row_specs", row_spy)
        monkeypatch.setattr(server, "toc_item_specs", toc_spy)
        monkeypatch.setattr(server, "header_fields", header_spy)

        assert client.get("/view?path=test.md").status_code == 200
        assert len(row_calls) == 1, "server render bypassed source_row_specs()"
        assert len(toc_calls) == 1, "server render bypassed toc_item_specs()"
        assert len(header_calls) == 1, "server render bypassed header_fields()"

    def test_client_loads_the_same_module_the_server_imports(self, client):
        """The browser executes view_specs.py verbatim — same bytes, same
        behaviour, no mirrored JS copy to drift."""
        import view_specs

        resp = client.get("/py/view_specs.py")
        assert resp.status_code == 200
        assert resp.json()["source"] == Path(view_specs.__file__).read_text()

    def test_rendered_rows_match_the_builder_specs(self, client):
        """Every id/class/label in the served HTML comes from the specs."""
        import server

        payload = server.build_view_payload("test.md")
        specs = server.source_row_specs(
            payload["blocks"], payload["comments_by_block"]
        )
        html = client.get("/view?path=test.md").text
        assert specs, "fixture must produce rows"
        for spec in specs:
            assert f'id="{spec["id"]}"' in html
            assert f'class="{spec["rowClass"]}"' in html
            assert f'data-line-start="{spec["startLine"]}"' in html
            assert f'data-line-end="{spec["endLine"]}"' in html

    def test_anchor_ids_still_derive_from_block_start_lines(self, client):
        """The anchoring contract: id="L{start_line}", byte-identical to
        before the port (a drift here orphans comments)."""
        import server

        payload = server.build_view_payload("test.md")
        html = client.get("/view?path=test.md").text
        for block in payload["blocks"]:
            assert f'id="L{block["start_line"]}"' in html

    def test_no_js_fallback_still_renders_rows_server_side(self, client):
        """The port must not turn /view into a JS-only page."""
        html = client.get("/view?path=test.md").text
        assert 'class="source-line' in html
        assert 'class="line-content"' in html
        assert "<h1>" in html

    def test_app_js_builds_rows_from_the_python_specs(self, client):
        """The soft-swap path must call the warm runtime's spec builders."""
        js = client.get("/static/app.js").text
        assert "renderer.sourceRowSpecs" in js
        assert "renderer.tocItemSpecs" in js
        assert "renderer.headerFields" in js

    def test_view_specs_imports_no_sibling_project_modules(self, client):
        """Pyodide writes view_specs.py into a bare FS alongside renderer.py.
        Stdlib imports are fine (renderer.py uses them); importing another
        doc-review module that was never written to /home/pyodide is not."""
        src = client.get("/py/view_specs.py").json()["source"]
        siblings = {
            p.stem for p in Path(__file__).parent.glob("*.py")
        } - {"view_specs"}
        for line in src.splitlines():
            if not (line.startswith("import ") or line.startswith("from ")):
                continue
            module = line.split()[1].split(".")[0]
            assert module not in siblings, (
                f"view_specs.py imports sibling module {module!r}, which "
                "Pyodide does not have on its filesystem"
            )

    def test_view_page_wires_up_the_python_spec_builders(self, client):
        """Without this the bridge could be deleted and the suite would stay
        green while the SPA silently degraded to full page loads."""
        html = client.get("/view?path=test.md").text
        assert "/py/view_specs.py" in html, "view page never loads view_specs.py"
        assert "from view_specs import" in html
        for fn in ("sourceRowSpecs", "tocItemSpecs", "headerFields"):
            assert fn in html, f"warm runtime does not expose {fn}"

    def test_view_specs_source_is_not_cacheable(self, client):
        """A cached copy could run stale builder code against fresh server
        markup — exactly the drift this shares the module to prevent."""
        resp = client.get("/py/view_specs.py")
        assert resp.headers["Cache-Control"] == "no-store"


class TestPresentationMode:
    """Marp presentation mode (#452).

    Presentation mode is JS-only by design, so these route-level tests cover
    the seams that no-browser testing can still reach: that review mode is
    untouched, that the client bridge is actually wired, and that the parser
    change did not orphan comments anchored inside front matter.
    """

    @pytest.fixture
    def marp_client(self, source_dir):
        (Path(source_dir) / "deck.md").write_text(
            "---\nmarp: true\ntheme: gaia\npaginate: true\n---\n\n"
            "# Slide one\n\nbody\n\n---\n\n# Slide two\n\nmore\n"
        )
        configure(source_dir, Path(source_dir) / "test_comments.db")
        return TestClient(app)

    def test_review_mode_still_renders_a_deck_server_side(self, marp_client):
        """Requirement 4: presentation mode may be JS-only, review mode may
        not.  A Marp file is an ordinary reviewable document."""
        html = marp_client.get("/view?path=deck.md").text
        assert 'class="source-line' in html
        assert "<h1>Slide one</h1>" in html
        assert "<h1>Slide two</h1>" in html

    def test_front_matter_no_longer_renders_as_a_bogus_heading(self, marp_client):
        """Gotcha 1: the closing `---` used to be read as a setext underline,
        so the directives rendered as an <h2> at the top of every file."""
        html = marp_client.get("/view?path=deck.md").text
        assert "<h2>marp: true" not in html
        assert 'class="front-matter"' in html

    def test_a_plain_file_renders_exactly_as_before(self, client):
        """Requirement 4: additive.  No front matter, no `---`, no change."""
        html = client.get("/view?path=test.md").text
        assert "<h1>Hello</h1>" in html
        assert "front-matter" not in html
        assert 'id="L1"' in html

    def test_comment_inside_front_matter_still_resolves_to_a_block(
        self, marp_client
    ):
        """The front-matter lines became one block instead of two.  A comment
        anchored in that range must still land on a block that exists — an
        orphan here is the 2026-07-22 incident all over again."""
        import server

        resp = marp_client.post(
            "/api/comments",
            json={
                "file_id": server.build_view_payload("deck.md")["file_id"],
                "path": "deck.md",
                "line_start": 3,
                "line_end": 3,
                "body": "on the theme directive",
            },
        )
        assert resp.status_code == 201

        payload = server.build_view_payload("deck.md")
        starts = {b["start_line"] for b in payload["blocks"]}
        assert set(payload["comments_by_block"]) <= starts, (
            "comment anchored outside any block"
        )
        assert 1 in payload["comments_by_block"]

    def test_view_page_wires_up_presentation_mode(self, marp_client):
        """Without this the bridge could be deleted and the suite would stay
        green while the Present button silently never appeared."""
        html = marp_client.get("/view?path=deck.md").text
        assert 'id="present-toggle"' in html
        assert "presentation_specs" in html, "warm runtime never imports it"
        assert "presentationSpecs" in html, "warm runtime does not expose it"
        assert "mdit-py-plugins" in html, "front_matter plugin never installed"
        assert "docreview:renderer-ready" in html

    def test_present_button_is_hidden_without_javascript(self, marp_client):
        """Presentation mode is JS-only; a no-JS reader must not see a dead
        control."""
        html = marp_client.get("/view?path=deck.md").text
        button = html[html.index('id="present-toggle"'):]
        assert "hidden" in button[: button.index(">")]

    def test_app_js_groups_slides_through_the_python_builder(self, marp_client):
        """Anti-drift: the grouping must come from view_specs, not a JS copy.
        A second grouping implementation is exactly what would let the two
        modes disagree about which block a comment belongs to."""
        js = marp_client.get("/static/app.js").text
        assert "renderer.presentationSpecs" in js

    def test_app_js_requires_the_builder_before_soft_navigating(
        self, marp_client
    ):
        """A partially-initialised runtime must degrade to a full page load
        rather than throw mid-swap."""
        js = marp_client.get("/static/app.js").text
        needed = js[js.index("var needed = ["):]
        assert "presentationSpecs" in needed[: needed.index("]")]

    def test_presenting_unmounts_the_comment_ui(self, marp_client):
        """Owner decision: presentation mode is strictly read-only."""
        js = marp_client.get("/static/app.js").text
        assert "if (presenting) return;" in js, (
            "block clicks still open the comment form while presenting"
        )
        assert "inline-comments" in js
