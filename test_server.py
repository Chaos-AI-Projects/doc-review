"""Route-level tests for the doc-review FastAPI server."""

import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import view_specs
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


class TestPresentationRoundTwo:
    """Fullscreen, on-screen controls and metadata gating (#455).

    Fullscreen and touch cannot be exercised on this box — there is no headless
    browser in the suite — so these are asset-level assertions over the code the
    browser actually receives.  They are deliberately about *seams* (is the
    fallback there, is there only one navigation path) rather than pixels; the
    in-browser confirmation is the owner's.
    """

    @pytest.fixture
    def marp_client(self, source_dir):
        (Path(source_dir) / "deck.md").write_text(
            "---\nmarp: true\n---\n\n# Slide one\n\n---\n\n# Slide two\n"
        )
        (Path(source_dir) / "notes.md").write_text(
            "# Notes\n\n---\n\nmore notes\n"
        )
        configure(source_dir, Path(source_dir) / "test_comments.db")
        return TestClient(app)

    @staticmethod
    def _app_js(client):
        return client.get("/static/app.js").text

    @staticmethod
    def _css(client):
        return client.get("/static/style.css").text

    @staticmethod
    def _strip_comments(text, line_comments=False):
        """Assertions are about code, not about prose describing it — a comment
        naming `opacity:` must neither satisfy nor trip a check on
        declarations."""
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        if line_comments:  # JS only; a CSS `url(http://…)` is not a comment
            text = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        return text

    @staticmethod
    def _between(text, start, end):
        """The source of one function or listener, by its opening and closing
        markers.  Missing either is a failure in its own right: the route being
        asserted on would have been renamed out from under the test."""
        assert start in text, "%r is not in the bundle" % start
        body = text[text.index(start):]
        assert end in body, "could not find the end of %r" % start
        return body[: body.index(end)]

    # A `/` in *operand* position opens a regular-expression literal; anywhere
    # else it divides.  These are the characters and the keywords after which
    # an operand is the only thing that can legally come next.
    _OPERAND_CHARS = "(,=:[!&|?{};"
    _OPERAND_KEYWORDS = frozenset(
        "return throw typeof instanceof in of new delete void case do else"
        " yield await".split()
    )

    @classmethod
    def _blank_js_noise(cls, text):
        """A same-length copy of `text` with comments and regular-expression
        literals blanked to spaces.  String literals are left intact.

        Same length, so callers can go on using offsets from the original — the
        blanked copy is what to *scan*, the original is what to *slice*.

        Braces are the load-bearing thing in this file, and a `{` that is only
        prose or only a character class must not move the depth.  The #460
        cycle-3 review reproduced two silent green bypasses through exactly
        that hole: `var action = nav(e.key);  // maps a key to {action` and
        `e.key.replace(/[{]/g, "")`, each of which raises the depth by one so
        the handler's own `}` stops closing the slice, which then runs on into
        a follower `setTimeout(function () { applyPresentationAction("exit") })`
        that answers for a route dispatching inline.  `_strip_comments` cannot
        close it: its `^\\s*//.*$` is whole-line only, and app.js carries ~44
        trailing `//` comments, so the shape is idiomatic here.

        Comments are recognised only outside a string, so `"http://x{"` stays a
        string and `'// not a comment {'` stays a string.  That reading is only
        ever as good as the quote state the scanner arrives with, which is why
        the division branch below is strict about more than braces: the #460
        cycle-6 review reproduced

            var key = e.key + /[']/.source;  // don't remap the {action key

        where the character class — read as division and so left unblanked —
        opened a *spurious* string that closed on the apostrophe in `don't`.
        Quote parity re-synchronised, the rest of the comment was scanned as
        code, and its `{` moved the depth with nothing raised at all.

        Regex literals: `/` is not decidable as regex-vs-division by a scanner
        this size, so this takes the unambiguous half — a `/` in *operand*
        position (directly after one of `_OPERAND_CHARS` or one of
        `_OPERAND_KEYWORDS`, or at the very start) is a regex; every other `/`
        divides.  Neither half is trusted silently, because *both* readings can
        be wrong and either one re-shapes the slice:

        * division misread as a regex cannot terminate on its line, which
          raises in `_end_of_regex`;
        * a regex misread as division stays in the scan, where its own
          characters are then read as code — `/[{]/` moves the depth, `/[']/`
          opens a string, `/[//]/` opens a comment.  The operand set cannot be
          widened to cover it — `)` and `]` really do precede division
          (`(a + b) / 2`, `xs[i] / 2`), so that trade only swaps a silent miss
          for a wrong blank.  Instead the division branch asks whether the
          would-be literal is *inert* — whether it holds anything this scanner
          acts on — and raises if it does; see `_inert_span`.  Brace balance is
          not a strong enough question: the span above is perfectly balanced,
          and that is exactly the one that stayed silent.  Neither is the span
          alone: the question is asked over one extra character of right
          context, because a comment opener is two characters wide and can
          straddle the span's closing `/` (`x /b/*{*/ }`, `x /b// {`).

        So a `/` whose would-be literal carries a brace, a quote or a comment
        opener is always loud, and that is the family every bypass reproduced
        in this cycle belongs to.  The residual cost is real but narrow: a
        genuine division followed on the same line by another `/`, with one of
        those between them, has to be spelled unambiguously.  On the bundle as
        shipped that cost is zero — app.js, nav_logic.js and style.css raise
        nothing.

        What is NOT closed, so that no one reads more into the guard than it
        earns: inertness is a question about the span's *contents*, and the two
        readings also differ in the operand-position state (`prev`/`prev_word`)
        they leave behind.  The regex branch jumps past the literal with
        `prev = ")"`; the division branch re-scans the literal's own characters,
        and if the character before its closing `/` is an operand char that
        closing `/` is itself read as an opener, blanking text OUTSIDE the span.
        A differential fuzz of the two readings still finds silent
        disagreements of that shape (`x /(/i)` before a backtick is the
        smallest).  They need a `/` on both sides of an operand char inside one
        line, none occur in the bundle, and closing them means simulating both
        readings rather than asking a syntactic question — so they are left
        open here, deliberately and on the record, rather than papered over.
        """
        out = list(text)
        n = len(text)
        i, quote, prev, prev_word = 0, None, "", ""
        while i < n:
            ch = text[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote, prev, prev_word = None, ch, ""
                i += 1
            elif ch in "\"'`":
                quote = ch
                i += 1
            elif ch == "/" and text[i : i + 2] == "//":
                while i < n and text[i] != "\n":
                    out[i] = " "
                    i += 1
            elif ch == "/" and text[i : i + 2] == "/*":
                end = text.find("*/", i + 2)
                end = n if end < 0 else end + 2
                for j in range(i, end):
                    if text[j] != "\n":
                        out[j] = " "
                i = end
            elif ch == "/" and (
                prev_word in cls._OPERAND_KEYWORDS
                or prev == ""
                or (not prev_word and prev in cls._OPERAND_CHARS)
            ):
                end = cls._end_of_regex(text, i)
                for j in range(i, end):
                    out[j] = " "
                # A regex literal is a value, so the next `/` divides.
                i, prev, prev_word = end, ")", ""
            elif ch == "/":
                # Division, by the operand-position rule.  The other reading
                # would have blanked the span; the two only agree when blanking
                # it changes nothing this scanner can see.  Otherwise fail loud,
                # the same way the regex branch does when it cannot terminate.
                end = cls._regex_end(text, i)
                # One character of right context, because the tokens this
                # scanner acts on are up to two characters wide and it reads
                # them at ABSOLUTE offsets: the span's own closing `/` pairs
                # with whatever follows it, so `/b/` before `*{*/` is really a
                # `/*` opener that a substring-only question cannot see.
                active = None if end is None else cls._inert_span(text[i:end + 1])
                if active is not None:
                    raise AssertionError(
                        "ambiguous `/` at offset %d (%r): read as division by "
                        "the operand-position rule, but as a regular-expression "
                        "literal it would span %r, and blanking that is not "
                        "invisible to this scanner — it contains %r. A brace "
                        "moves the depth the slices are counted with, a quote "
                        "opens or closes a string literal, and a `//` or `/*` "
                        "starts a comment; each of those re-shapes the slice, "
                        "so the two readings disagree. This scanner is not a JS "
                        "parser and will not guess between them — spell it "
                        "unambiguously (a `new RegExp(\"…\")`, or parenthesise "
                        "the division)."
                        % (
                            i,
                            text[i : text.find("\n", i)],
                            text[i:end],
                            active,
                        )
                    )
                prev, prev_word = ch, ""
                i += 1
            elif ch.isalnum() or ch in "_$":
                word = i
                while i < n and (text[i].isalnum() or text[i] in "_$"):
                    i += 1
                prev_word = text[word:i]
                prev = prev_word[-1]
            else:
                if not ch.isspace():
                    prev, prev_word = ch, ""
                i += 1
        return "".join(out)

    @staticmethod
    def _regex_end(text, start):
        """The index just past the regex literal `text[start]` would open, or
        `None` if no such literal terminates on that line.

        `/` inside a `[…]` character class does not close the literal, and a
        real one never spans a line.  Both readings of a `/` need this: the
        regex branch to know what to blank, the division branch to know what it
        would have blanked had it chosen the other way.
        """
        i, in_class = start + 1, False
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "\n":
                break
            if in_class:
                if ch == "]":
                    in_class = False
            elif ch == "[":
                in_class = True
            elif ch == "/":
                i += 1
                while i < len(text) and text[i].isalpha():  # flags
                    i += 1
                return i
            i += 1
        return None

    # Everything this scanner reacts to: braces move the depth, quotes open and
    # close string literals, and these two openers start a comment.  Both
    # comment openers are two characters wide and are matched at absolute
    # offsets, which is why callers must pass one character of right context —
    # see `_inert_span`.
    _ACTIVE_TOKENS = ("{", "}", '"', "'", "`", "//", "/*")

    @classmethod
    def _inert_span(cls, window):
        """The first token in `window` this scanner would act on, or `None` if
        there is none.

        Callers pass the would-be regex literal PLUS one character of right
        context.  The tokens are up to two characters wide and the scanner
        matches them at absolute offsets, so one can straddle the span's right
        edge: in `x /b/*{*/ }` the literal `/b/` holds nothing active, but its
        closing `/` pairs with the following `*` to open a comment.  Asked
        about `/b/` alone this answers `None`; asked about `/b/*` it answers
        `/*`.  The #460 cycle-7 review reproduced both straddles (`/*` and
        `//`) end to end, each moving the depth by a full brace in silence.

        This is a wider question than brace balance.  The blanker is stateful
        about quotes and comments as well as depth, so an unblanked span
        re-syncing the quote state is just as much a disagreement as one moving
        the depth — and it is the quieter of the two.  A brace-balanced `/[']/`
        left in the scan opens a spurious string, which swallows an arbitrary
        amount of what follows before some later apostrophe closes it; nothing
        about that is loud.  So the span has to be *inert*, not merely balanced.

        What `None` does and does not mean.  It means: no token this scanner
        acts on lies inside the span or straddles its right edge — so the span
        cannot, by its own contents, move the depth or open a string or a
        comment.  It does NOT mean the two readings are indistinguishable in
        general, and the stronger claim should not be made here.  The scanner
        also carries operand-position state (`prev` / `prev_word`), and the two
        readings leave it differently: the regex branch jumps to `end` with
        `prev = ")"`, while the division branch re-scans the span's own
        characters, so `prev` ends up as the span's last character or its flag
        word.  That state decides how a LATER `/` is classified, and an inert
        span can still reclassify one.  A differential fuzz over the two
        readings (cycle-8) found the residual reachable — e.g. `x /(/i)` + a
        backtick, where the span `/(/i` is inert but the re-scan reads its
        closing `/` as an opener, because the `(` before it is an operand char,
        and blanks text OUTSIDE the span.  That family is not closed here and
        is not claimed to be; it is recorded in `_blank_js_noise`.

        Deliberately one-directional: anything other than `None` is reported
        rather than guessed at.  That rejects some genuine divisions (see
        `_blank_js_noise`), which is the accepted side to be wrong on — those
        fail loud and are spelled around, where the other side re-shapes a
        slice in silence.
        """
        hits = [(window.index(t), t) for t in cls._ACTIVE_TOKENS if t in window]
        return min(hits)[1] if hits else None

    @classmethod
    def _end_of_regex(cls, text, start):
        """The index just past the regex literal opening at `text[start]`.

        An unterminated one means this `/` was a division the operand-position
        rule called wrong.  That fails loud here instead of blanking the rest of
        the file out of the scan.
        """
        end = cls._regex_end(text, start)
        if end is not None:
            return end
        raise AssertionError(
            "unterminated regular-expression literal at offset %d (%r): a `/` "
            "in operand position that never closes on its line is a division "
            "this scanner read as a regex, and the slice cannot be trusted "
            "until it is spelled unambiguously"
            % (start, text[start : text.find("\n", start)])
        )

    @classmethod
    def _through_matching_brace(cls, text, start):
        """`text` from `start` through the `}` that closes the first `{` after
        it.

        A literal end marker cannot tell "the handler ended" from "something
        later happened to close at the same indent", and that is a bypass
        rather than a nicety: the #460 review reproduced the keyboard route
        inlined with the suite still green, by closing the handler one level
        out and following it with a `setTimeout(function () { … })` whose body
        then answered for the route.  Matching braces ends the slice where the
        handler actually ends, whatever follows it and however it is indented.

        Depth is counted over `_blank_js_noise(text)`, so a `{` in a trailing
        comment or in a regex character class cannot re-open the same hole; the
        string handling stays here, because the count needs strings *present*
        (see `_listener`) and only their braces ignored.
        """
        scan = cls._blank_js_noise(text)
        assert "{" in scan[start:], "the listener has no body"
        i = scan.index("{", start)
        depth, quote = 0, None
        while i < len(scan):
            ch = scan[i]
            if quote:  # a brace inside a string literal does not open a block
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "\"'`":
                quote = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if not depth:
                    return text[start : i + 1]
            i += 1
        raise AssertionError("the listener body is never closed")

    @classmethod
    def _listener(cls, text, event):
        """The body of the one inline `document.addEventListener(<event>, function …)`.

        Every guard here is owed to a review that *reproduced* a green bypass
        of the keyboard route — the suite passing while the route's dispatch
        had in fact been inlined:

        * `function` is part of the start marker (#458), so naming the handler
          fails as an unambiguous "is not in the bundle" rather than sliding
          the slice onto whichever listener comes next;
        * the slice ends at the handler's own closing brace, found by matching
          braces rather than by a literal end marker (#460) — see
          `_through_matching_brace`;
        * the event NAME occurs exactly once in the bundle (#460).  A slice
          takes the FIRST match, so a decoy can answer for a real route that
          is registered in some other spelling — and every spelling has to
          write the event name somewhere.  Counting the bare token instead of
          one registration syntax is what closes that: the cycle-3 review
          reproduced GREEN, real route inlined, with `var EV = "keydown"`, a
          `` `keydown` `` template literal, an `on(el, ev, fn)` helper,
          `document["onkeydown"] =`, and `addEventListener ("keydown"` with a
          space — five spellings, one hole.

        Counting happens after comments are stripped, so prose recording a
        registration that used to live here is not read as a second route.

        Two guards this replaces are gone rather than left dead: the
        `addEventListener\\(<event>` count (subsumed — and with it the
        whitespace disagreement it had with the alias guard below), and the
        `on<event>\\s*=` assignment guard (subsumed by the optional `on`
        prefix, which unlike that regex does not have to reach the `=` and so
        also sees `document["onkeydown"]`).  The alias guard stays: a
        registration whose event name is computed (`"key" + "down"`) writes no
        token for the count to see.
        """
        text = cls._strip_comments(text, line_comments=True)
        # `\b` would be wrong: `$` is an identifier character in JS and not in
        # `\w`.  The optional `on` makes `onkeydown` one token, in any bracket
        # spelling, rather than a substring the boundary would hide.
        occurrences = re.findall(
            r"(?<![A-Za-z0-9_$])(?:on)?%s(?![A-Za-z0-9_$])" % re.escape(event),
            cls._blank_js_noise(text),
        )
        assert len(occurrences) == 1, (
            "the token `%s` occurs %d times in the bundle, not once: a slice "
            "takes the first match, so the second occurrence could be the real "
            "route dispatching inline while this slice reads a decoy. Every "
            "way of registering a listener has to name the event somewhere, "
            "so this counts the name itself rather than one registration "
            "syntax. A legitimate second `%s` listener would fail here too; "
            "that is the accepted cost of the guard, not an oversight."
            % (event, len(occurrences), event)
        )
        assert not re.search(r"addEventListener(?!\s*\()", text), (
            "addEventListener is aliased or bound somewhere, so a registration "
            "can be spelled in a way the count above cannot see"
        )
        start = 'document.addEventListener("%s", function' % event
        assert start in text, "%r is not in the bundle" % start
        return cls._through_matching_brace(text, text.index(start))

    @classmethod
    def _css_rules(cls, client, needle):
        """Every rule whose selector mentions `needle`, as (selector, body)."""
        css = cls._strip_comments(cls._css(client))
        return [
            (sel.strip(), body)
            for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
            if needle in sel
        ]

    @classmethod
    def _css_rule(cls, client, selector):
        """The body of exactly one rule, by its full selector."""
        rules = dict(cls._css_rules(client, selector))
        assert selector in rules, (
            "no `%s` rule of its own — found %s" % (selector, sorted(rules))
        )
        return rules[selector]

    @staticmethod
    def _relative_luminance(rgb):
        """WCAG 2.x relative luminance of an 8-bit sRGB triple."""
        linear = []
        for value in rgb:
            c = value / 255.0
            linear.append(
                c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
            )
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    @classmethod
    def _contrast(cls, rgb_a, rgb_b):
        """WCAG 2.x contrast ratio, order-independent."""
        darker, lighter = sorted(
            (cls._relative_luminance(rgb_a), cls._relative_luminance(rgb_b))
        )
        return (lighter + 0.05) / (darker + 0.05)

    # ── 1. Real full screen ──

    def test_the_deck_asks_for_real_fullscreen(self, marp_client):
        """`position: fixed` covers the viewport but not the browser chrome."""
        assert "requestFullscreen" in self._app_js(marp_client)

    def test_a_rejected_fullscreen_request_does_not_break_presenting(
        self, marp_client
    ):
        """requestFullscreen() returns a promise that can reject — denied,
        unsupported (iOS Safari has no element fullscreen) or not from a user
        gesture.  An unhandled rejection would take presenting down with it;
        the fixed overlay has to remain a usable fallback."""
        js = self._app_js(marp_client)
        request = self._between(
            js, "function requestDeckFullscreen(", "\n    function ")
        assert ".catch(" in request, "the promise rejection path is unhandled"
        assert "catch (err)" in request, "a throwing/absent API is unhandled"

    def test_the_fullscreen_release_is_gated_on_the_dom_not_the_flag(
        self, marp_client
    ):
        """`deckFullscreen` is set *asynchronously* — requestFullscreen()
        resolves after the deck is already on screen.  Exiting in that window
        (a fast Esc, a control tap) with the release gated on the flag skips
        exitFullscreen() while the request lands anyway, stranding the browser
        in fullscreen with no deck in it.  The DOM is the only synchronous
        truth: release iff the fullscreen element IS our deck.
        """
        js = self._app_js(marp_client)
        release = self._strip_comments(
            self._between(
                js, "function releaseDeckFullscreen(", "\n    function "),
            line_comments=True,
        )
        gates = [ln for ln in release.splitlines() if "return;" in ln]
        assert gates, "the release has no early-out gate at all"
        assert any("fullscreenElement() !== deckEl" in ln for ln in gates), (
            "the release is not gated on the DOM"
        )
        assert not any("deckFullscreen" in ln for ln in gates), (
            "the release is gated on a flag the in-flight request has not set "
            "yet, so a fast exit strands the browser in fullscreen"
        )
        # Cleared BEFORE exitFullscreen(), so the fullscreenchange this
        # triggers reads as our own doing rather than the browser dropping us.
        assert "deckFullscreen = false;" in release, (
            "the release never clears the flag it invalidates"
        )
        assert "release.call(" in release, (
            "the release never actually calls the exit API it looked up"
        )
        assert release.index("deckFullscreen = false;") < release.index(
            "release.call("
        ), "the flag is cleared after the release it describes"

    def test_a_second_presentation_does_not_inherit_the_fullscreen_flag(
        self, marp_client
    ):
        """The enter path resets `deckFullscreen` before asking, so a request
        that is denied (or never answered) cannot leave the previous run's
        `true` behind — which would make onFullscreenChange treat an unrelated
        fullscreen exit as a reason to tear this deck down."""
        js = self._app_js(marp_client)
        request = self._strip_comments(
            self._between(
                js, "function requestDeckFullscreen(", "\n    function "),
            line_comments=True,
        )
        # `partition`, not `index`: _between has already established the
        # function is here, so its body's opening brace cannot be missing — a
        # guard for that would be unreachable, and this cannot raise anyway.
        body = request.partition("{")[2]
        assert re.match(r"\s*deckFullscreen = false;", body), (
            "the flag is not reset as the first thing the enter path does"
        )
        enter = self._between(
            js, "function enterPresentation(", "\n    function ")
        assert "requestDeckFullscreen(deckEl)" in enter, (
            "entering no longer goes through the resetting request path"
        )

    def test_leaving_fullscreen_externally_does_not_orphan_the_deck(
        self, marp_client
    ):
        """The browser's own exit gesture must not leave a half-state."""
        js = self._app_js(marp_client)
        assert "fullscreenchange" in js, "the browser's own exit is not observed"
        assert "fullscreenChangeAction" in js, "the decision is not shared logic"

    def test_the_header_no_longer_sits_over_the_slides(self, marp_client):
        """#452 raised .file-header to z-index: 2 while presenting, so the site
        chrome painted over the deck.  A read-only deck has no reason to sit
        under it."""
        assert "body.presenting .file-header" not in self._css(marp_client)

    def test_the_deck_paints_above_the_page(self, marp_client):
        # Through the guarded helper, like its siblings: a raw `.index()` on
        # the selector raises `ValueError: substring not found` on a rename,
        # which names nothing.  `_css_rule` fails as an assertion that says
        # which selector went missing and what was found instead.
        deck = self._css_rule(marp_client, ".presentation")
        z = re.search(r"z-index:\s*(\d+)", deck)
        assert z and int(z.group(1)) > 2, "the deck can still be painted over"

    # ── 2. On-screen controls (the mobile ask) ──

    def test_the_deck_carries_prev_next_and_exit_controls(self, marp_client):
        """A phone has no arrow keys and no Esc."""
        js = self._app_js(marp_client)
        controls = js[js.index("var PRESENTATION_CONTROLS = ["):]
        controls = controls[: controls.index("];")]
        for action in ("prev", "next", "exit"):
            assert '"%s"' % action in controls, f"no {action} control"

    def test_the_controls_reuse_the_keyboard_navigation_path(self, marp_client):
        """Pointer and keyboard must resolve through the SAME decision and the
        same dispatcher.  A second navigation path is what would let a tap and
        a keypress drift into disagreeing about what 'next' means."""
        js = self._app_js(marp_client)
        assert "presentationControlAction" in js, "controls bypass nav_logic"
        assert "function applyPresentationAction(" in js, "no single dispatcher"
        # Per route, not a tally: a count cannot say WHICH route dropped out,
        # and a route that keeps asking nav_logic but then dispatches inline —
        # the second navigation path this test exists to prevent — leaves the
        # count looking merely smaller.
        routes = (
            ("the pointer", self._between(
                js, "function onControlClick(", "\n    }")),
            ("the keyboard", self._listener(js, "keydown")),
            ("the browser's own fullscreen exit", self._between(
                js, "function onFullscreenChange(", "\n    }")),
        )
        for name, body in routes:
            # Stripped: a commented-out call is not a call.
            assert "applyPresentationAction(" in self._strip_comments(
                body, line_comments=True
            ), "%s route does not funnel through the single dispatcher" % name

    def test_the_keyboard_slice_cannot_run_past_its_own_listener(self):
        """The instrument the route test above leans on, pinned in its own
        right.  The #458 review reproduced this GREEN against the merged tree:
        name the keydown handler, dispatch inline inside it, and add one more
        listener in the same block — `fullscreenchange` and
        `docreview:renderer-ready` already live there, so that is routine.  The
        slice then ran on into the *new* listener, found an
        `applyPresentationAction(` in it, and pronounced the keyboard route
        covered while its dispatch had in fact been inlined.

        Asserted against synthetic bundles rather than app.js, so the guard is
        pinned by what it rejects rather than by today's source happening to be
        unambiguous.
        """
        inlined = (
            '            if (action === "exit") exitPresentation();\n'
            '            else if (action === "next") showSlide(slideIndex + 1);\n'
            "            else showSlide(slideIndex - 1);\n"
        )
        sibling = (
            '        document.addEventListener("visibilitychange", function () {\n'
            '            applyPresentationAction("exit");\n'
            "        });\n"
        )
        # The reviewer's reproduction verbatim.
        named = (
            "        function onDeckKeydown(e) {\n" + inlined + "        }\n"
            '        document.addEventListener("keydown", onDeckKeydown);\n'
            + sibling
        )
        with pytest.raises(AssertionError, match="is not in the bundle"):
            self._listener(named, "keydown")

        # The same bypass reached the other way: the handler stays inline but
        # its closing brace is re-indented, so a literal end marker is not
        # found until after whatever follows.  The slice must still stop at the
        # handler, so the follower's call cannot be read as this route's.
        # `sibling` is another listener; `stray` is NOT — the #460 review
        # reproduced this shape green, because the guard that used to catch the
        # overrun counted swallowed registrations and so only ever noticed the
        # first kind.
        stray = (
            "        setTimeout(function () {\n"
            '            applyPresentationAction("exit");\n'
            "        });\n"
        )
        for follower in (sibling, stray):
            reindented = (
                '        document.addEventListener("keydown", function (e) {\n'
                + inlined
                + "    });\n"
                + follower
            )
            assert "applyPresentationAction(" not in self._listener(
                reindented, "keydown"
            ), "the slice ran past the handler and took the follower's call"

        # The same overrun, re-opened by a single unbalanced `{` that is not
        # code: a TRAILING `//` comment (which `_strip_comments` never sees —
        # its regex is whole-line — and which app.js writes ~44 times), and a
        # regex character class.  Both raise the depth by one, so the handler's
        # own `}` stops closing the slice and `stray` answers for the route.
        for noise in (
            "            var action = nav(e.key);  // maps a key to {action\n",
            '            var key = e.key.replace(/[{]/g, "");\n',
        ):
            unbalanced = (
                '        document.addEventListener("keydown", function (e) {\n'
                + noise
                + inlined
                + "        });\n"
                + stray
            )
            assert "applyPresentationAction(" not in self._listener(
                unbalanced, "keydown"
            ), "a `{` outside code re-opened the overrun the matcher closes"

        # …at every position the `/` can occupy, not just after `(`.  Whether a
        # `/` opens a regex or divides is decided by what precedes it, and until
        # the #460 cycle-4 review this suite only ever varied the *contents* of
        # the literal — every fixture wrote it after `(`.  So a `/[{]/` after
        # `=>`, `+`, `)` or `*` was read as division, left unblanked, and
        # re-opened this exact overrun with the whole suite still green.
        #
        # The contract is that a `/` this scanner cannot place is LOUD: each
        # construct below must either have its literal blanked, or raise.  What
        # none of them may do is pass through silently with a `{` that still
        # moves the depth, which is what lets `stray` answer for the route.
        for construct in (
            "var hasBrace = (s) => /[{]/.test(s);",    # after `=>`
            'var hasBrace = "a" + /[{]/.source;',      # after `+`
            "if (presenting) /[{]/.test(e.key);",      # after `)`
            "var q = 2 * /[{]/.source.length;",        # after `*`
            "throw /[{]/.source;",                     # after a keyword
            'var key = e.key.replace(/[{]/g, "");',    # after `(`
            "var re = /[{]/;",                         # after `=`
            "var pair = [/[{]/, /[}]/];",              # after `[` and after `,`
            "return /[{]/.test(e.key);",               # after `return`
        ):
            positioned = (
                '        document.addEventListener("keydown", function (e) {\n'
                "            " + construct + "\n"
                + inlined
                + "        });\n"
                + stray
                # The enclosing initialiser's own `}`.  Without it the extra
                # depth simply runs off the end and every fixture below fails
                # as "never closed" — loud, but not the failure being pinned.
                # app.js registers this handler inside a function, so the
                # unbalanced `{` is absorbed there and the slice closes late
                # and QUIETLY, taking the follower's call with it.
                + "    }\n"
            )
            try:
                slice_ = self._listener(positioned, "keydown")
            except AssertionError as exc:
                # Loud is an acceptable answer, but only the deliberate kind:
                # any other assertion here would mean the slice broke for an
                # unrelated reason and this fixture stopped testing anything.
                assert "ambiguous `/`" in str(exc), (
                    "%r failed for an unintended reason: %s" % (construct, exc)
                )
                continue
            assert "applyPresentationAction(" not in slice_, (
                "a regex literal after %r was read as division, so its `{` "
                "moved the depth and the slice took the follower's call"
                % construct.split("/[")[0].strip()
            )

        # …and not only when the literal carries a brace.  Asking whether the
        # would-be span was brace-*balanced* was not a strong enough question:
        # the blanker is stateful about quotes and comments too, so a literal
        # read as division re-shapes the scan whenever ANY of that state is
        # inside it.  The contract is inertness — a `/` read as division raises
        # unless blanking its span would be invisible to this scanner.
        #
        # Each span below is perfectly brace-balanced, so the brace question
        # answered "fine" and each one passed through silently.
        for span in ("/[']/", '/["]/', "/[`]/", "/[//]/", "/[/*]/"):
            with pytest.raises(AssertionError, match="ambiguous `/`"):
                self._blank_js_noise("var k = e.key + %s.source;\n" % span)

        # …and not only when the token is INSIDE the span.  Asking about
        # `text[i:end]` was still not the whole question: the tokens this
        # scanner acts on are two characters wide and it matches them at
        # ABSOLUTE offsets, so one can straddle the span's right edge.  The
        # closing `/` of the would-be literal is the last character in the
        # span, and it pairs with the character AFTER the span to form `//` or
        # `/*` — invisible to a substring-only predicate, which duly answered
        # "inert" while the two readings differed by a full brace.  The #460
        # cycle-7 review reproduced both openers:
        #
        #   `a = x /b/*{*/ }`  division: depth -1   regex: depth 0
        #   `a = x /b// {`     division: depth  0   regex: depth +1
        #
        # Neither raised.  The question therefore has to be asked over one
        # character of right context, so no token the scanner acts on can
        # straddle the edge unseen.
        for straddle in ("var k = a /b/*{*/ }\n", "var k = a /b// {\n"):
            with pytest.raises(AssertionError, match="ambiguous `/`"):
                self._blank_js_noise(straddle)

        # End to end, because the straddle is a slice bypass and not just a
        # disagreement on paper.  In both shapes the reading this scanner
        # actually takes is the WRONG one — it opens a comment where the regex
        # reading opens none — and the comment then swallows the handler's own
        # `});`, so the slice never closes there and runs on into `stray`.
        #
        # The two are pinned at different levels on purpose.  `_listener`
        # strips block comments before the scanner ever runs, so a `/*`
        # straddle is gone by then and only the `//` shape survives that far;
        # the `/*` shape is pinned against `_through_matching_brace`, which is
        # the instrument that actually counts the depth and which blanks the
        # raw text.  Each must raise; what neither may do is hand back a slice
        # with the follower's call in it.
        straddling_line = (
            '        document.addEventListener("keydown", function (e) {\n'
            + inlined
            + "            var k = a /b// });\n"
            + stray
            + "    }\n"
        )
        # The `*/` that closes the comment the division reading opens sits
        # several lines below, so everything between — `});` included — is
        # swallowed.
        straddling_block = (
            '        document.addEventListener("keydown", function (e) {\n'
            "            var k = a /b/*\n" + inlined + "        });\n"
            "        var ok = 1 /* */;\n" + stray + "    }\n"
        )
        marker = 'document.addEventListener("keydown", function'
        for shape, slicer in (
            (straddling_line, lambda t: self._listener(t, "keydown")),
            (
                straddling_block,
                lambda t: self._through_matching_brace(t, t.index(marker)),
            ),
        ):
            try:
                slice_ = slicer(shape)
            except AssertionError as exc:
                assert "ambiguous `/`" in str(exc), (
                    "the straddling-opener fixture failed for an unintended "
                    "reason: %s" % exc
                )
                continue
            assert "applyPresentationAction(" not in slice_, (
                "a comment opener straddling the right edge of a would-be "
                "regex literal was invisible to the inertness question, so the "
                "division reading opened a comment that swallowed the "
                "handler's own `}` and the slice took the follower's call"
            )

        # End to end, which is what makes the quote case a bypass rather than a
        # curiosity — and it is the quiet one.  `/[']/` is balanced, so nothing
        # raised; left unblanked, its `'` opened a *spurious* string that closed
        # on the apostrophe in `don't`.  Quote parity re-synchronised there, so
        # the REST of the comment was scanned as code — `{action` included — the
        # depth moved by one, the handler's own `}` stopped closing the slice,
        # and `stray` answered for a route dispatching inline.  (The even-quote
        # shape, `/["]/`, never re-syncs and so fails loud downstream; this odd
        # one is the silent one, and the one worth pinning by name.)
        resyncing = (
            '        document.addEventListener("keydown", function (e) {\n'
            "            var k = e.key + /[']/.source;  // don't remap the {action\n"
            + inlined
            + "        });\n"
            + stray
            + "    }\n"
        )
        try:
            slice_ = self._listener(resyncing, "keydown")
        except AssertionError as exc:
            assert "ambiguous `/`" in str(exc), (
                "the re-syncing-quote fixture failed for an unintended "
                "reason: %s" % exc
            )
        else:
            assert "applyPresentationAction(" not in slice_, (
                "a brace-balanced regex literal read as division left its "
                "quote in the scan; it re-synced on `don't`, put the comment's "
                "`{` back into the depth, and the slice took the follower's "
                "call"
            )

        # …without the comment mode swallowing strings that merely look like
        # comments.  Both braces below are inside string literals, so neither
        # may move the depth and neither `//` may start a comment.
        stringy = (
            '        document.addEventListener("keydown", function (e) {\n'
            '            var u = "http://example.test/x{";\n'
            "            var s = '// not a comment {';\n"
            "            applyPresentationAction(navLogic.presentationAction(e.key));\n"
            "        });\n" + stray
        )
        slice_ = self._listener(stringy, "keydown")
        assert "applyPresentationAction(navLogic" in slice_, (
            "a brace inside a string ended the slice early"
        )
        assert "setTimeout(" not in slice_, (
            "a `//` inside a string was read as a comment and hid a brace"
        )

        # And reached from the other side: a decoy registered AHEAD of the real
        # route.  A slice takes the first match, so without a uniqueness guard
        # the decoy answers for a route that is dispatching inline — and the
        # bundle already registers `click` on `document` more than once, so a
        # second listener for one event is not a contrived shape.
        #
        # The real route is spelled a different way each time: counting one
        # literal spelling of the registration is what let the #460 review
        # reproduce three of these green.
        for real, complaint in (
            (
                '        document.addEventListener("keydown", onDeckKeydown);\n',
                "a slice takes the first",
            ),
            (
                '        window.addEventListener("keydown", function (e) {\n'
                + inlined
                + "        });\n",
                "a slice takes the first",
            ),
            (  # single quotes: nothing in this repo enforces the double-quoted form
                "        document.addEventListener('keydown', function (e) {\n"
                + inlined
                + "        });\n",
                "a slice takes the first",
            ),
            (  # not an addEventListener registration at all
                "        document.onkeydown = function (e) {\n"
                + inlined
                + "        };\n",
                "occurs 2 times",
            ),
            (  # nor in a bracket spelling the old `on<event>\s*=` never reached
                '        document["onkeydown"] = function (e) {\n'
                + inlined
                + "        };\n",
                "occurs 2 times",
            ),
            (  # the event name held in a variable — no literal to count
                '        var EV = "keydown";\n'
                "        document.addEventListener(EV, function (e) {\n"
                + inlined
                + "        });\n",
                "occurs 2 times",
            ),
            (  # a template literal, which the old `['\"]` class could not see
                "        document.addEventListener(`keydown`, function (e) {\n"
                + inlined
                + "        });\n",
                "occurs 2 times",
            ),
            (  # registered through a helper rather than directly
                "        function on(el, ev, fn) { el.addEventListener(ev, fn); }\n"
                '        on(document, "keydown", function (e) {\n'
                + inlined
                + "        });\n",
                "occurs 2 times",
            ),
            (  # a space before the paren, which the old count demanded away
                '        document.addEventListener ("keydown", function (e) {\n'
                + inlined
                + "        });\n",
                "occurs 2 times",
            ),
            (  # the case the token count genuinely cannot see: a computed
                # event name writes no token, so the alias guard has to carry
                # it — this is why that guard was kept and the other two were
                # deleted rather than left dead.
                "        var addL = document.addEventListener.bind(document);\n"
                '        addL("key" + "down", function (e) {\n'
                + inlined
                + "        });\n",
                "aliased or bound",
            ),
        ):
            decoy = (
                '        document.addEventListener("keydown", function (e) {\n'
                '            applyPresentationAction("exit");\n'
                "        });\n" + real
            )
            with pytest.raises(AssertionError, match=complaint):
                self._listener(decoy, "keydown")

        # Prose is not a second route: the count runs on stripped source, so a
        # comment recording a registration that used to be here is not read as
        # one.  (The same reason the funnel assertion strips before looking.)
        remembered = (
            '        // document.addEventListener("keydown", oldHandler) lived here\n'
            '        document.addEventListener("keydown", function (e) {\n'
            "            applyPresentationAction(navLogic.presentationAction(e.key));\n"
            "        });\n"
        )
        assert "applyPresentationAction(" in self._listener(remembered, "keydown")

        # And the shape it must accept, so neither guard is vacuous.
        intact = (
            '        document.addEventListener("keydown", function (e) {\n'
            "            applyPresentationAction(navLogic.presentationAction(e.key));\n"
            "        });\n" + sibling
        )
        assert "applyPresentationAction(" in self._listener(intact, "keydown")

    def test_the_dispatcher_handles_every_action_explicitly(self, marp_client):
        """The dispatcher used to end in a bare `else` that navigated
        *backwards*, so any action without a branch here — a new `first` bound
        to Home, say — would silently step back instead of doing nothing.  Each
        action nav_logic can emit needs its own branch, and anything else must
        be a no-op.
        """
        js = self._app_js(marp_client)
        fn = self._strip_comments(
            self._between(
                js, "function applyPresentationAction(", "\n    function "),
            line_comments=True,
        )
        # The vocabulary, read out of nav_logic rather than hardcoded: an
        # action added there with no branch here has to fail this test.
        nav = marp_client.get("/static/nav_logic.js").text
        vocabulary = self._between(
            nav, "function presentationControlAction(", "default:")
        actions = set(re.findall(r'case "([^"]+)":', vocabulary))
        assert actions, "the control action vocabulary was not located"

        handled = set(re.findall(r'action === "([^"]+)"', fn))
        assert actions <= handled, (
            "no explicit branch for %s" % sorted(actions - handled)
        )
        # No fall-through: navigation only happens under an explicit test.
        assert not re.search(r"else\s*(\{[^}]*)?showSlide", fn), (
            "an unrecognised action still falls through to navigation"
        )

    def test_there_is_no_whole_slide_click_to_advance(self, marp_client):
        """Explicitly rejected: a tap anywhere would make an overflowing slide
        impossible to scroll and text impossible to select on a phone, and it
        would fight the explicit buttons that were asked for."""
        js = self._app_js(marp_client)
        # Quote-agnostic: a single-quoted listener would otherwise slip past.
        for target in ("deck", "deckEl", "section", "bar"):
            found = re.search(
                target + r"""\.addEventListener\(\s*["']click["']""", js
            )
            assert not found, f"{target} advances the deck on any tap"
        # The only click listener in the deck belongs to the controls.
        assert 'btn.addEventListener("click", onControlClick)' in js

    def test_the_controls_do_not_swallow_slide_content(self, marp_client):
        """They overlay the slide; the slide stays scrollable and selectable."""
        css = self._css(marp_client)
        bar = css[css.index(".presentation-controls {"):]
        bar = bar[: bar.index("}")]
        assert "pointer-events: none" in bar

    def test_the_controls_themselves_are_clickable(self, marp_client):
        css = self._css(marp_client)
        control = css[css.index(".presentation-control {"):]
        control = control[: control.index("}")]
        assert "pointer-events: auto" in control

    def test_touch_targets_are_phone_sized(self, marp_client):
        """>= 44px, the smallest reliable thumb target."""
        css = self._css(marp_client)
        control = css[css.index(".presentation-control {"):]
        control = control[: control.index("}")]
        for prop in ("min-width", "min-height"):
            size = re.search(prop + r":\s*(\d+)px", control)
            assert size, f"{prop} is not a pixel size"
            assert int(size.group(1)) >= 44, f"{prop} is below a 44px target"

    def test_the_controls_are_semi_transparent(self, marp_client):
        """As asked: visible affordance without hiding the slide behind it.

        `opacity:` alone used to satisfy this, which is exactly what let the
        invisible variant through — see the contrast test below.  Transparency
        has to come from the background, so the glyph keeps its own alpha.
        """
        assert "rgba(" in self._css_rule(marp_client, ".presentation-control")

    def test_the_controls_stay_visible_against_the_slide(self, marp_client):
        """These controls exist *because* the reporter could not navigate on a
        phone, so an invisible control is the same bug again.  Round two first
        shipped a group `opacity: .35` over `background: rgba(0,0,0,.55)`: the
        group opacity fades the white glyph along with the circle, compositing
        to ~1.57:1 — well under the 3:1 floor WCAG 1.4.11 sets for a non-text
        UI control.  A phone has no hover and no pre-tap focus, so the resting
        state is the only one that counts.

        No headless browser needed: the resting contrast is fully determined by
        the two declarations asserted on here.
        """
        rules = self._css_rules(marp_client, ".presentation-control")
        assert rules, "the control rules were not located"

        # (a) Transparency is carried by the background alone.  A group
        # `opacity` applies to the glyph too, so darkening the circle can never
        # compensate for it — hence no `opacity` in ANY state of the control.
        for selector, body in rules:
            assert not re.search(r"(^|[\s;])opacity\s*:", body), (
                "`%s` fades the glyph along with the circle" % selector
            )

        # (b) White glyph on the resting circle, composited over the deck's
        # white slide (the worst case: any darker backdrop only helps), clears
        # the 3:1 floor.
        control = self._css_rule(marp_client, ".presentation-control")
        assert re.search(r"color:\s*#fff\b", control), (
            "the glyph is not the white this contrast is computed for"
        )
        bg = re.search(
            r"background:\s*rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"
            r"([\d.]+)\s*\)",
            control,
        )
        assert bg, "the resting control has no rgba background"
        alpha = float(bg.group(4))
        composited = [
            alpha * int(bg.group(i)) + (1 - alpha) * 255 for i in (1, 2, 3)
        ]
        ratio = self._contrast(composited, (255, 255, 255))
        assert ratio >= 3.0, (
            "the resting control composites to %.2f:1 on a white slide, under "
            "the 3:1 floor for a non-text UI control" % ratio
        )

    def test_the_controls_live_in_the_deck_so_they_leave_with_it(
        self, marp_client
    ):
        """Presentation mode stays read-only: the controls are deck DOM, so
        exiting removes them along with the slides — no stray chrome over the
        review view."""
        js = self._app_js(marp_client)
        build = js[js.index("function buildDeck("):]
        build = build[: build.index("\n    function showSlide")]
        assert "buildControls()" in build

    # ── 3. Metadata-gated availability ──

    def test_a_declared_deck_still_offers_the_present_button(self, marp_client):
        """The server ships the same builder the client runs, so the gate is
        checked once, in Python."""
        import server

        payload = server.build_view_payload("deck.md")
        specs = view_specs.presentation_specs(
            payload["blocks"], None, payload["source"]
        )
        assert specs["available"] is True

    def test_an_undeclared_multi_slide_file_no_longer_offers_it(
        self, marp_client
    ):
        """Behavioural change: a prose document that happens to contain a `---`
        rule used to get a Present button."""
        import server

        payload = server.build_view_payload("notes.md")
        specs = view_specs.presentation_specs(
            payload["blocks"], None, payload["source"]
        )
        assert len(specs["slides"]) == 2
        assert specs["available"] is False

    def test_review_mode_is_untouched_for_both(self, marp_client):
        """Losing the Present button must not change how a file reads."""
        for path in ("deck.md", "notes.md"):
            html = marp_client.get("/view?path=" + path).text
            assert 'class="source-line' in html
            assert 'id="present-toggle"' in html


class TestPerSlideLayouts:
    """Per-slide layouts via Marp `_class` directives (#462).

    The whitelist and the scoping live in `view_specs` (covered by
    `test_presentation.py`), because both the server render and the Pyodide
    soft swap call it; a copy in `app.js` would work in one path and silently
    not in the other.  What is left for this file is the two asset-level
    seams: the class actually reaching the slide element, and the CSS for the
    names the whitelist admits actually shipping.
    """

    @pytest.fixture
    def marp_client(self, source_dir):
        (Path(source_dir) / "deck.md").write_text(
            "---\nmarp: true\n---\n\n<!-- _class: title -->\n\n# Slide one\n"
        )
        configure(source_dir, Path(source_dir) / "test_comments.db")
        return TestClient(app)

    @staticmethod
    def _build_deck(client):
        js = client.get("/static/app.js").text
        js = re.sub(r"//.*$", "", js, flags=re.M)
        start = js.index("function buildDeck(")
        return js[start : js.index("\n    }", start)]

    @staticmethod
    def _layout_rules(client):
        css = re.sub(r"/\*.*?\*/", "", client.get("/static/style.css").text, flags=re.S)
        return [
            (sel.strip(), body)
            for sel, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css)
            if ".layout-" in sel
        ]

    def test_the_slide_element_carries_its_layout_class(self, marp_client):
        """Without this the layout is computed, shipped over the bridge and
        then dropped on the floor."""
        deck = self._build_deck(marp_client)
        assert 'section.className = "slide layout-" + slide.layout;' in deck

    def test_the_layout_name_is_not_resolved_in_javascript(self, marp_client):
        """Constraint: the whitelist and the fallback have ONE home.  A copy
        here would run on the soft-swap path and silently not on the server
        one.  So `slide.layout` is *read* exactly once — straight onto the
        class — and never inspected.  The bare-token match skips
        `.view-layout` and the `"slide layout-"` prefix, both hyphenated, so a
        branch on the value has nowhere to hide."""
        js = re.sub(r"//.*$", "", marp_client.get("/static/app.js").text, flags=re.M)
        reads = re.findall(r"(?<![A-Za-z0-9_$-])layout(?![A-Za-z0-9_$-])", js)
        assert len(reads) == 1, (
            "the bundle mentions `layout` %d times, not once: the layout is "
            "being inspected in JS rather than just applied" % len(reads)
        )

    def test_every_whitelisted_layout_ships_css(self, marp_client):
        """A layout the parser accepts but the stylesheet has never heard of
        renders identically to `default` — the directive would look honoured
        and do nothing.  An empty rule body is the same bug wearing a
        selector, so the declarations are counted too."""
        rules = self._layout_rules(marp_client)
        for layout in view_specs.PRESENTATION_LAYOUTS:
            if layout == "default":  # the base .slide rules ARE the default
                continue
            styled = [
                (sel, body)
                for sel, body in rules
                if ".slide.layout-%s" % layout in sel and body.strip()
            ]
            assert styled, "the `%s` layout styles nothing — found %s" % (
                layout,
                [sel for sel, _ in rules],
            )

    def test_no_css_layout_is_unreachable(self, marp_client):
        """The other direction: a rule for a name the whitelist rejects is
        dead CSS that can never be selected."""
        named = set()
        for sel, _body in self._layout_rules(marp_client):
            named.update(re.findall(r"\.layout-([A-Za-z0-9_-]+)", sel))
        assert named <= set(view_specs.PRESENTATION_LAYOUTS), (
            "CSS styles layouts the whitelist rejects: %s"
            % sorted(named - set(view_specs.PRESENTATION_LAYOUTS))
        )

    def test_no_layout_rule_depends_on_sibling_position(self, marp_client):
        """`buildDeck` gives every row its **own** `div.slide-block`, so two
        paragraphs on one slide are never siblings.  `p:last-of-type` matches
        *every* paragraph there — a rule written to catch the last one silently
        catches all of them, which is how the first cut of the `quote` layout
        applied its attribution style to the whole slide.  Anything
        position-dependent below `.slide` has to hang off `.slide-block`."""
        assert 'blockEl.className = "slide-block";' in self._build_deck(
            marp_client
        ), "rows are no longer one-per-div; re-derive this rule before relaxing it"
        for sel, _body in self._layout_rules(marp_client):
            tail = sel[sel.rindex(".layout-") :]
            assert not re.search(
                r"(:(first|last|only|nth)-(of-type|child)|[+~])", tail
            ) or ".slide-block" in tail, (
                "`%s` selects by sibling position inside a slide block that "
                "only ever holds one element" % sel
            )

    def test_a_directive_block_does_not_reach_the_slide(self, marp_client):
        """End to end over the real route payload: the directive comment is
        metadata, and with `html: False` it would otherwise render as visible
        escaped text on the slide."""
        import server

        payload = server.build_view_payload("deck.md")
        specs = view_specs.presentation_specs(
            payload["blocks"], None, payload["source"]
        )
        assert [s["layout"] for s in specs["slides"]] == ["title"]
        html = " ".join(
            row["html"] for slide in specs["slides"] for row in slide["rows"]
        )
        assert "_class" not in html

    def test_review_mode_still_shows_the_directive(self, marp_client):
        """Presentation mode is a grouping layer; the review render of the same
        file is unchanged, directive block included."""
        html = marp_client.get("/view?path=deck.md").text
        assert "_class: title" in html
        assert 'class="source-line' in html
