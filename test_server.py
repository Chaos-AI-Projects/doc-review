"""Route-level tests for the doc-review FastAPI server."""

import os
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
    """One list per block, threaded root-then-replies (#402, re-threaded by #465 Phase 2).

    #402 removed the separate ``reply_map`` and the nested ``.reply-thread``
    markup, and that stays gone.  What Phase 2 changes is the *order* within
    that single list: a reply now follows the root it answers instead of
    sitting wherever the clock put it.
    """

    def test_all_comments_in_block_including_former_replies(self, client, source_dir):
        """TDD anchor (a): a block with three comments (including one with parent_id set)
        returns all three in comments_by_block, each reply under its root, and the view
        context no longer contains reply_map."""
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

        # The reply follows the root it answers, which is c1 — so it comes
        # before c2, which was written earlier but starts a thread of its own.
        ids = [c["id"] for c in block_comments]
        assert ids == [c1["id"], c3["id"], c2["id"]], \
            f"Expected thread order {[c1['id'], c3['id'], c2['id']]}, got {ids}"
        assert [c["depth"] for c in block_comments] == [0, 1, 0]

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


class TestGitPathspecs:
    def test_a_filename_that_looks_like_a_flag_is_not_read_as_one(
        self, git_source_dir
    ):
        """`git ls-files --error-unmatch -x.md` parses the name as `-x .md`.

        `-x` is `--exclude`, so the pathspec disappears entirely, nothing is
        left for `--error-unmatch` to fail on, and the command reports success
        for a file git has never heard of.  A leading `--` ends option parsing,
        which is why the sibling `diff`/`blame` calls already have one.
        """
        from server import _git_head_if_clean

        odd = Path(git_source_dir) / "-x.md"
        odd.write_text("never added to git\n")
        assert _git_head_if_clean(odd) is None, (
            "an untracked file must not report a clean HEAD"
        )

    def test_a_dirty_file_named_like_an_option_is_still_reported_dirty(self, tmp_path):
        """The same `--` fix at the other call site, which needs a different shape.

        Repeating the test above against `_is_tracked_and_dirty()` would be
        vacuous: `-x.md` parses as `-x .md`, so `--error-unmatch` is left with no
        pathspec and exits 0 — but *this* function's `diff` call keeps its own
        `--`, so it answers correctly anyway and the missing one never shows.
        Measured on all three git states, `-x.md` gives the same answer with and
        without it.

        What does move is a name git rejects as an **unknown option** (`--foo.md`),
        on a file that is **tracked and dirty**: `ls-files` exits non-zero, the
        guard reads that as "not tracked", and True becomes False.  Note this is
        the opposite state from the sibling test above — there the bug shows on an
        untracked file, here only on a tracked one.

        False is the unsafe direction.  It is what lets `_resolve_comment_blocks()`
        write a block id from a line the blame migration has not corrected, which
        this gate exists to prevent and which no later view can undo.
        """
        from server import _is_tracked_and_dirty

        odd = tmp_path / "--foo.md"
        odd.write_text("original\n")
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")
        _git(tmp_path, "add", "--", "--foo.md")
        _git(tmp_path, "commit", "-qm", "commit an option-shaped filename")
        odd.write_text("original\nuncommitted edit\n")

        assert _is_tracked_and_dirty(odd) is True, (
            "a tracked file with uncommitted changes is dirty whatever it is "
            "called; reading its name as a git option loses the tracked check"
        )

    def test_a_filename_that_is_not_utf8_does_not_crash_the_guards(self, tmp_path):
        """git quotes the offending name back on stderr, in the bytes it got.

        POSIX filenames are bytes, so a name like ``bad\\xff.md`` is legal on
        disk and arrives as a surrogate-escaped str.  `ls-files --error-unmatch`
        echoes it into stderr verbatim, and capturing that stream in text mode
        raises `UnicodeDecodeError` while decoding output nobody reads — from
        inside the guard, so it escapes as a 500 on a plain page view rather
        than as the "no git evidence" answer the guard is supposed to give.

        Both guards must return their falsy answer instead of raising.
        """
        from server import _git_head_if_clean, _is_tracked_and_dirty

        odd = tmp_path / os.fsdecode(b"bad\xff.md")
        odd.write_bytes(b"git will never decode my name\n")
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")

        assert _git_head_if_clean(odd) is None
        assert _is_tracked_and_dirty(odd) is False


class TestGitGuardsStayDistinct:
    """`_is_tracked_and_dirty()` is not the negation of `_git_head_if_clean()`.

    The two share their plumbing — the `ls-files --error-unmatch --` tracked
    check, the `diff --quiet HEAD --` comparison, the timeout handling — which
    makes collapsing one into the other look like a tidy simplification.  It is
    not: they answer different questions, and the cases below are the ones where
    the answers come apart.  Both would pass vacuously if the guards agreed
    everywhere, so each asserts the *pair*.
    """

    def test_an_untracked_file_has_no_clean_head_and_is_not_dirty(self, tmp_path):
        """No git behind the file at all — falsy from both, but not the same answer.

        `_git_head_if_clean()` says None because there is no commit to anchor to.
        `_is_tracked_and_dirty()` must say False, and the distinction is what the
        backfill gate rides on: dirty means "a reverse-blame correction is still
        owed to this line, do not freeze it into a block id", and an untracked
        file is never owed one.  Answering True here would stall the backfill
        forever on files git will never have an opinion about.
        """
        from server import _git_head_if_clean, _is_tracked_and_dirty

        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")
        (tmp_path / "committed.md").write_text("committed\n")
        _git(tmp_path, "add", "--", "committed.md")
        _git(tmp_path, "commit", "-qm", "give the repo a HEAD")

        stranger = tmp_path / "stranger.md"
        stranger.write_text("git has never seen this\n")

        assert _git_head_if_clean(stranger) is None, (
            "an untracked file cannot report a clean HEAD"
        )
        assert _is_tracked_and_dirty(stranger) is False, (
            "an untracked file is not dirty; it is outside git's opinion "
            "entirely, and no blame migration will ever correct its lines"
        )

    def test_a_repo_with_no_commits_has_no_clean_head_and_nothing_dirty(
        self, tmp_path
    ):
        """A tracked file in a repo with no HEAD: `diff HEAD` cannot even run.

        `git diff --quiet HEAD -- f` exits non-zero here because HEAD does not
        resolve, not because the file differs from anything.  Reading that exit
        code as "dirty" is the mistake `_is_tracked_and_dirty()`'s extra
        `rev-parse --verify HEAD` exists to prevent, so the pair is asserted
        against a repo that has staged a file and never committed it.
        """
        from server import _git_head_if_clean, _is_tracked_and_dirty

        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")
        staged = tmp_path / "staged.md"
        staged.write_text("staged, never committed\n")
        _git(tmp_path, "add", "--", "staged.md")

        assert _git_head_if_clean(staged) is None, (
            "there is no HEAD to be clean against"
        )
        assert _is_tracked_and_dirty(staged) is False, (
            "a repo with no commits has nothing for the blame migration to "
            "arrive from, so its files must not be held back as dirty"
        )


class TestBlameSurvivesNonUtf8Content:
    """The two git calls that do not go through `_git_run()` decode blame output.

    `_blame_surviving_lines()` and the `/api/blame` route run git themselves —
    they carry 10s and 15s timeouts against `_git_run()`'s hardcoded 5s, so they
    were left out of the shared runner rather than folded into it.  That also
    left them out of its `errors="replace"`.

    Unlike the guards, these do not merely echo a pathspec back: `blame
    --porcelain` prints **the file's own content**, so an ordinary ASCII
    filename is enough to break them.  One stray byte anywhere in a tracked
    `.md` — a latin-1 dash pasted from a mail client, a truncated multi-byte
    sequence — and strict decoding raises inside `subprocess.run`, before either
    caller can apply its own error handling.

    That byte is otherwise harmless: every other reader on the view path takes
    the source with `errors="replace"`, so the page renders it as U+FFFD and
    carries on.  These two are the last strict decoders left, which is what
    turns a cosmetic byte into a 500.
    """

    BAD_DOC = b"# Title\n\npara one\n\npara two\n\nsigned \xff Ren\xe9\n"

    def _repo(self, tmp_path: Path) -> tuple[Path, str, str]:
        """A repo whose `doc.md` holds a non-UTF-8 byte, with two commits."""
        (tmp_path / "doc.md").write_bytes(self.BAD_DOC)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")
        _git(tmp_path, "add", "--", "doc.md")
        _git(tmp_path, "commit", "-qm", "initial doc")
        anchor = _git(tmp_path, "rev-parse", "HEAD")

        (tmp_path / "doc.md").write_bytes(b"preamble\n\n" + self.BAD_DOC)
        _git(tmp_path, "add", "--", "doc.md")
        _git(tmp_path, "commit", "-qm", "insert a preamble")
        head = _git(tmp_path, "rev-parse", "HEAD")
        return tmp_path / "doc.md", anchor, head

    def test_reverse_blame_answers_instead_of_raising(self, tmp_path):
        """`_blame_surviving_lines()` must report the move, not die reading it.

        Its docstring promises the surviving line numbers, or None when blame
        failed — a caller relying on that None leaves the stored anchor alone.
        A `UnicodeDecodeError` is neither: it escapes the function entirely and
        takes the whole page render with it, on the backfill path, for a comment
        the file itself displays fine.

        Asserting the migrated numbers rather than "did not raise" keeps the
        test honest: swallowing the decode into a blanket None would satisfy the
        weaker claim while silently freezing every anchor in the file.
        """
        from server import _blame_surviving_lines

        doc, anchor, head = self._repo(tmp_path)

        # Lines 5..7 at the anchor are 'para two', '', 'signed \xff René'; the
        # preamble pushes them to 7..9.  The range has to cover the bad byte —
        # `-L` bounds which lines blame prints, so a byte outside it is never
        # decoded and the test would pass against the unfixed code.
        assert _blame_surviving_lines(doc, anchor, head, 5, 7) == [7, 8, 9]

    def test_the_blame_route_answers_instead_of_returning_500(self, tmp_path):
        """`GET /api/blame` on such a file must serve the blame, not fail.

        The route already maps every way git can disappoint it — 502 on a
        timeout or a missing binary, 404 on a non-zero exit.  A decode error is
        none of those and reaches the client as an unhandled 500, on a file git
        blamed perfectly well.
        """
        self._repo(tmp_path)
        configure(str(tmp_path), tmp_path / "test_comments.db")
        resp = TestClient(app).get("/api/blame?path=doc.md")

        assert resp.status_code == 200, resp.text
        signed = [
            line for line in resp.json()["lines"]
            if line["content"].startswith("signed ")
        ]
        assert signed and "\ufffd" in signed[0]["content"], (
            "the undecodable byte must come back replaced, the way every other "
            "reader on the view path already renders it"
        )


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


# ── Content-derived block anchoring (#465, Phase 1) ──────────────────────


DOC_465 = "# Title\n\nfirst paragraph\n\ntarget paragraph\n\nlast paragraph\n"

# A block that spans several lines, so a comment can start part-way into it.
# Blocks: L1 "# Title", L3-5 the paragraph, L7 "last paragraph".
DOC_465_MULTILINE = "# Title\n\nalpha\nbeta\ngamma\n\nlast paragraph\n"


@pytest.fixture
def anchored(tmp_path):
    """A configured server over a single non-git markdown file."""
    doc = tmp_path / "doc.md"
    doc.write_text(DOC_465)
    db_path = tmp_path / "comments.db"
    configure(str(tmp_path), db_path)
    return {
        "dir": tmp_path,
        "doc": doc,
        "db": db_path,
        "client": TestClient(app),
    }


def _payload_comments(payload):
    return [c for group in payload["comments_by_block"].values() for c in group]


def _group_of(payload, comment_id):
    """The block start_line a comment is grouped under, or None if dropped."""
    for start, group in payload["comments_by_block"].items():
        for c in group:
            if c["id"] == comment_id:
                return start
    return None


def _find(payload, comment_id):
    for c in _payload_comments(payload):
        if c["id"] == comment_id:
            return c
    return None


def _post_json_comment(client, *, path="doc.md", line_start, line_end=None, body="hi"):
    resp = client.post(
        "/api/comments",
        json={
            "file_id": "fid",
            "path": path,
            "line_start": line_start,
            "line_end": line_end if line_end is not None else line_start,
            "author": "tester",
            "body": body,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _block_id_of(source, line):
    from renderer import render_markdown_blocks

    for b in render_markdown_blocks(source):
        if b["start_line"] <= line <= b["end_line"]:
            return b["block_id"]
    return None


class TestBlockIdPersistence:
    def test_json_route_stores_block_id(self, anchored):
        created = _post_json_comment(anchored["client"], line_start=5)
        assert created["block_id"] == _block_id_of(DOC_465, 5)

    def test_form_route_stores_block_id(self, anchored):
        resp = anchored["client"].post(
            "/comment",
            data={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "via form",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        listed = anchored["client"].get("/api/comments?path=doc.md").json()
        assert listed[0]["block_id"] == _block_id_of(DOC_465, 5)


class TestBlockIdResolution:
    def test_comment_follows_a_block_that_moved(self, anchored):
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5)
        # Insert two lines above the commented block: it slides 5 -> 7.
        anchored["doc"].write_text("preamble line\n\n" + DOC_465)
        payload = build_view_payload("doc.md")
        assert _group_of(payload, created["id"]) == 7
        assert _find(payload, created["id"])["detached"] is False

    def test_comment_on_deleted_block_is_flagged_detached(self, anchored):
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5)
        anchored["doc"].write_text("# Title\n\nfirst paragraph\n\nlast paragraph\n")
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert comment is not None, "a comment must never be dropped from the view"
        assert comment["detached"] is True

    def test_detached_comment_lands_on_a_real_block(self, anchored):
        """A group key that is not a block start_line renders nowhere (#465)."""
        from server import build_view_payload

        _post_json_comment(anchored["client"], line_start=5)
        anchored["doc"].write_text("only one paragraph now\n")
        payload = build_view_payload("doc.md")
        starts = {b["start_line"] for b in payload["blocks"]}
        assert set(payload["comments_by_block"]) <= starts

    def test_duplicate_blocks_keep_their_own_comments(self, anchored):
        from server import build_view_payload

        dup = "dup para\n\nmiddle\n\ndup para\n"
        anchored["doc"].write_text(dup)
        first = _post_json_comment(anchored["client"], line_start=1, body="on first")
        second = _post_json_comment(anchored["client"], line_start=5, body="on second")
        anchored["doc"].write_text("new heading\n\n" + dup)
        payload = build_view_payload("doc.md")
        assert _group_of(payload, first["id"]) == 3
        assert _group_of(payload, second["id"]) == 7

    def test_reported_range_shifts_with_the_block(self, anchored):
        """A range keeps its length when its text moves (#465 review).

        The range may span more than the anchor block, so it travels by the
        block's displacement rather than collapsing onto the block.
        """
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=3, line_end=5)
        anchored["doc"].write_text("preamble line\n\n" + DOC_465)  # everything +2
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert (comment["line_start"], comment["line_end"]) == (5, 7)

    def test_reported_range_never_runs_past_the_file(self, anchored):
        """A shift towards the top of a shortened file stays inside it."""
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5, line_end=7)
        anchored["doc"].write_text("intro\n\ntarget paragraph\n")  # 3 lines
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert (comment["line_start"], comment["line_end"]) == (3, 3)
        assert comment["line_end"] <= len(payload["source"].splitlines())

    def test_a_detached_comment_never_names_a_line_the_file_lost(self, anchored):
        """A stale line outlives the file that was long enough for it (#468).

        Nothing in the unmatched branch touches the stored line, so a
        truncation leaves the comment reported at ``L7`` of a 3-line file and
        ``app.js`` renders that verbatim — a line the reader cannot scroll to.
        ``detached`` already says the anchor is lost; the line beside it should
        still name text the file actually has.

        Asserted on the last line exactly rather than as ``<= total``: a bound
        would also be satisfied by clamping everything to line 1, which loses
        the only signal left about where the comment used to sit.
        """
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=7, line_end=7)
        anchored["doc"].write_text("# Title\n\nonly paragraph\n")  # 3 lines
        payload = build_view_payload("doc.md")
        source = payload["source"].splitlines()
        comment = _find(payload, created["id"])
        assert comment["detached"] is True, "the commented text is gone"
        assert (comment["line_start"], comment["line_end"]) == (3, 3), (
            f"reported L{comment['line_start']}-{comment['line_end']} of a "
            f"{len(source)}-line file"
        )
        assert source[comment["line_start"] - 1] == "only paragraph", (
            "the reported line must name a line the file has"
        )
        api = [
            c
            for c in anchored["client"].get("/api/comments?path=doc.md").json()
            if c["id"] == created["id"]
        ][0]
        assert (api["line_start"], api["line_end"]) == (3, 3), (
            "the surface agents read through must be clamped too (#495)"
        )

    def test_a_stale_range_is_clamped_even_when_its_start_still_lands(self, anchored):
        """The other half of the unmatched branch (#468).

        Here the stored line still falls inside some block, so the comment is
        placed by line rather than flagged past the end — but its *end* was
        written against a longer file and is passed through just as untouched.
        """
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5, line_end=7)
        # 5 lines, and the text at L5 is replaced, so the stored block_id no
        # longer matches: the comment resolves through the unmatched branch.
        anchored["doc"].write_text("# Title\n\nfirst paragraph\n\nreplaced text\n")
        payload = build_view_payload("doc.md")
        source = payload["source"].splitlines()
        comment = _find(payload, created["id"])
        assert comment["detached"] is True, "the block it was written about is gone"
        assert (comment["line_start"], comment["line_end"]) == (5, 5), (
            f"reported L{comment['line_start']}-{comment['line_end']} of a "
            f"{len(source)}-line file"
        )

    def test_a_clamped_range_keeps_the_end_of_it_that_the_file_still_has(
        self, anchored
    ):
        """Only the end runs past the file, so only the end may move (#468).

        The two tests above both use ranges whose start and end clamp to the
        same line, so neither can tell ``line_start`` apart from ``line_end``:
        sourcing the start from the end passes them both.  Collapsing the start
        onto the file's last line is the mirror of collapsing it onto line 1 —
        it discards where the comment began, which the file still has.
        """
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=1, line_end=99)
        # L1's text changes too, so the stored block_id matches nothing and the
        # comment resolves through the unmatched branch — but L1 still exists.
        anchored["doc"].write_text("# New Title\n\nonly paragraph\n")  # 3 lines
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert comment["detached"] is True
        assert (comment["line_start"], comment["line_end"]) == (1, 3), (
            f"reported L{comment['line_start']}-{comment['line_end']}; the start "
            "is a line the file still has and must survive the clamp"
        )

    def test_a_legacy_row_is_clamped_even_though_it_is_not_detached(self, anchored):
        """The unmatched branch also serves rows that carry no ``block_id``.

        A pre-#465 row resolves by line and is reported *attached*, so a clamp
        that only ran for detached comments would leave this one naming a line
        the file lost — and without the badge that at least explains it.
        """
        from db import create_comment, get_connection, init_db
        from server import build_view_payload

        conn = get_connection(anchored["db"])
        init_db(conn)
        row = create_comment(
            conn,
            file_id="fid",
            line_start=1,
            line_end=99,
            author="legacy",
            body="pre-#465 comment",
            file_path="doc.md",
        )
        conn.close()
        assert row["block_id"] is None
        anchored["doc"].write_text("# Title\n\nonly paragraph\n")  # 3 lines
        payload = build_view_payload("doc.md")
        comment = _find(payload, row["id"])
        assert comment["detached"] is False, "a legacy row resolves by line"
        assert (comment["line_start"], comment["line_end"]) == (1, 3), (
            f"reported L{comment['line_start']}-{comment['line_end']} of a 3-line "
            "file with no detached badge to explain it"
        )

    def test_an_emptied_file_still_reports_a_first_line(self, anchored):
        """``total_lines`` is 0, and a line number below 1 is not a line.

        An emptied file still renders one block, so the comment is placed
        rather than dropped; clamping without the ``max(1, ...)`` floor would
        report ``L0``, which is the same defect in the other direction.
        """
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5, line_end=7)
        anchored["doc"].write_text("")
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert comment["detached"] is True
        assert (comment["line_start"], comment["line_end"]) == (1, 1), (
            f"reported L{comment['line_start']}-{comment['line_end']} of an empty file"
        )

    def test_range_inside_a_block_keeps_its_offset_when_nothing_changed(self, anchored):
        """A comment part-way into a block does not snap to the block start.

        Every other range assertion here starts a comment exactly on a block's
        first line, where "shift by the block's displacement" and "move to the
        block start" are the same number.  They are not the same for a comment
        written about the middle of a paragraph, and with the file untouched the
        reported range must not move at all.
        """
        from server import build_view_payload

        anchored["doc"].write_text(DOC_465_MULTILINE)
        created = _post_json_comment(anchored["client"], line_start=4, line_end=5)
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert (comment["line_start"], comment["line_end"]) == (4, 5)
        assert comment["detached"] is False

    def test_range_inside_a_block_travels_by_the_blocks_displacement(self, anchored):
        """The offset into the block survives the block moving."""
        from server import build_view_payload

        anchored["doc"].write_text(DOC_465_MULTILINE)
        created = _post_json_comment(anchored["client"], line_start=4, line_end=5)
        # Two lines above the block: it slides 3 -> 5, so the range goes 4-5 -> 6-7.
        anchored["doc"].write_text("preamble line\n\n" + DOC_465_MULTILINE)
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert (comment["line_start"], comment["line_end"]) == (6, 7)

    def test_a_comment_keeps_its_text_when_its_blank_run_collapses(self, anchored):
        """A block can keep its id and still get shorter.

        `_normalize_raw` collapses blank-line runs, so a fenced block whose
        blank lines are squeezed out hashes the same while losing lines.  The
        offset is stored in that same normalized space, so it stays true.

        This asserts the *text* at the reported line, not merely that the number
        landed inside the file: with a raw offset the comment resolved to the
        unrelated tail paragraph, which is inside the file and entirely wrong.
        """
        from server import build_view_payload

        roomy = "# T\n\n```py\na = 1\n\n\n\n\nb = 2\n```\n\nunrelated tail\n"
        squeezed = "# T\n\n```py\na = 1\n\nb = 2\n```\n\nunrelated tail\n"
        anchored["doc"].write_text(roomy)
        created = _post_json_comment(anchored["client"], line_start=9, line_end=9)

        anchored["doc"].write_text(squeezed)
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        source = payload["source"].splitlines()
        assert source[comment["line_start"] - 1] == "b = 2"
        assert comment["line_end"] <= len(source), "the range ran past the file"
        assert comment["detached"] is False
        # The fence is L3-L7 now: the comment must stay inside the block it is
        # grouped under, not merely inside the file.
        assert _group_of(payload, created["id"]) == 3
        assert comment["line_start"] <= 7

    def test_a_comment_keeps_its_text_when_its_blank_run_grows(self, anchored):
        """The same units mismatch, in the other direction.

        Adding blank lines inside a fence does not change its id, so a raw
        offset stopped short and pointed at a blank line — and reported the
        comment as attached, which is worse than saying the anchor was lost.
        """
        from server import build_view_payload

        tight = "# T\n\n```py\na = 1\n\nb = 2\ntarget\n```\n\ntail\n"
        grown = "# T\n\n```py\na = 1\n\n\n\nb = 2\ntarget\n```\n\ntail\n"
        anchored["doc"].write_text(tight)
        created = _post_json_comment(anchored["client"], line_start=7, line_end=7)

        anchored["doc"].write_text(grown)
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        source = payload["source"].splitlines()
        assert source[comment["line_start"] - 1] == "target"
        assert comment["detached"] is False

    def test_an_offset_that_names_no_line_in_the_block_keeps_the_stored_line(
        self, anchored
    ):
        """Blame is evidence, the offset is an estimate — evidence wins.

        An offset that resolves to no line of the matched block cannot be
        trusted to sub-block precision: it was written under a different
        convention, or against an interior that has since changed.  Placing it
        anyway (by clamping to the file, as the first cut did) overrides a
        stored line the blame migration may well have got right, and reports the
        result as attached.  The stored line stands instead.
        """
        import sqlite3

        from server import build_view_payload

        anchored["doc"].write_text(DOC_465_MULTILINE)
        created = _post_json_comment(anchored["client"], line_start=4, line_end=4)
        with sqlite3.connect(anchored["db"]) as conn:
            conn.execute(
                "UPDATE comments SET block_offset = 99 WHERE id = ?", (created["id"],)
            )

        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        source = payload["source"].splitlines()
        assert source[comment["line_start"] - 1] == "beta"
        assert (comment["line_start"], comment["line_end"]) == (4, 4)

    def test_a_row_with_an_id_but_no_offset_keeps_its_stored_line(self, anchored):
        """Rows written between the two halves of #465 have an id and no offset.

        A server running the first cut of this branch stored `block_id` without
        `block_offset`.  Such a row can still be grouped by its id, but there is
        no offset to place it inside the block, and inventing one (0, say) would
        move a range its author never moved.  It keeps the line it has.
        """
        from db import get_connection
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5, line_end=5)
        conn = get_connection(anchored["db"])
        conn.execute(
            "UPDATE comments SET block_offset = NULL WHERE id = ?", (created["id"],)
        )
        conn.commit()
        conn.close()

        anchored["doc"].write_text("preamble line\n\n" + DOC_465)
        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        assert _group_of(payload, created["id"]) == 7, "the id still finds the block"
        assert (comment["line_start"], comment["line_end"]) == (5, 5)

    def test_resolution_is_idempotent(self, anchored):
        from server import build_view_payload

        created = _post_json_comment(anchored["client"], line_start=5)
        anchored["doc"].write_text("preamble line\n\n" + DOC_465)
        first = build_view_payload("doc.md")
        second = build_view_payload("doc.md")
        assert _group_of(first, created["id"]) == _group_of(second, created["id"])
        assert _find(second, created["id"])["block_id"] == created["block_id"]


class TestLegacyRowsAndBackfill:
    def _legacy(self, anchored, *, line_start=5, line_end=5):
        from db import create_comment, get_connection, init_db

        conn = get_connection(anchored["db"])
        init_db(conn)
        row = create_comment(
            conn,
            file_id="fid",
            line_start=line_start,
            line_end=line_end,
            author="legacy",
            body="pre-#465 comment",
            file_path="doc.md",
        )
        conn.close()
        assert row["block_id"] is None
        return row

    def test_legacy_row_still_renders(self, anchored):
        from server import build_view_payload

        row = self._legacy(anchored)
        payload = build_view_payload("doc.md")
        assert _group_of(payload, row["id"]) == 5
        assert _find(payload, row["id"])["detached"] is False

    def test_legacy_row_is_backfilled_once(self, anchored):
        from db import get_comment, get_connection
        from server import build_view_payload

        row = self._legacy(anchored)
        build_view_payload("doc.md")

        conn = get_connection(anchored["db"])
        stored = get_comment(conn, row["id"])
        conn.close()
        assert stored["block_id"] == _block_id_of(DOC_465, 5)
        assert stored["updated_at"] == row["updated_at"]

        # Backfilled rows then follow their block like any other comment.
        anchored["doc"].write_text("preamble line\n\n" + DOC_465)
        payload = build_view_payload("doc.md")
        assert _group_of(payload, row["id"]) == 7

    def test_legacy_row_past_end_of_file_is_not_backfilled(self, anchored):
        """No block contains the line, so there is no id to learn."""
        from db import get_comment, get_connection
        from server import build_view_payload

        row = self._legacy(anchored, line_start=400, line_end=400)
        payload = build_view_payload("doc.md")
        comment = _find(payload, row["id"])
        assert comment is not None
        assert comment["detached"] is True

        conn = get_connection(anchored["db"])
        assert get_comment(conn, row["id"])["block_id"] is None
        conn.close()

    def test_comment_between_blocks_is_not_lost(self, anchored):
        """Line 4 is the blank line separating two blocks."""
        from server import build_view_payload

        row = self._legacy(anchored, line_start=4, line_end=4)
        payload = build_view_payload("doc.md")
        starts = {b["start_line"] for b in payload["blocks"]}
        assert _group_of(payload, row["id"]) in starts


class TestBlockIdWithBlameMigration:
    """The two anchors must not both be applied to the same displacement.

    `_migrate_comment_anchors()` *persists* a moved `line_start`, so by the time
    the block-id step runs the stored range has already travelled.  Positioning
    the comment by adding the block's displacement on top of that counts the
    move twice.  Every other range test uses the non-git `anchored` fixture,
    where blame is a no-op and the double count cannot appear.
    """

    def _post(self, git_client, git_source_dir, line_start, line_end):
        from file_id import derive_file_id

        resp = git_client.post(
            "/api/comments",
            json={
                "file_id": derive_file_id(str(Path(git_source_dir) / "doc.md")),
                "path": "doc.md",
                "line_start": line_start,
                "line_end": line_end,
                "author": "reviewer",
                "body": "on para two",
            },
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def test_a_committed_move_is_counted_once(self, git_client, git_source_dir):
        from server import build_view_payload

        created = self._post(git_client, git_source_dir, 5, 5)  # 'para two'
        doc = Path(git_source_dir) / "doc.md"
        doc.write_text("preamble\n\n" + GIT_DOC)  # 'para two' L5 -> L7
        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "insert a preamble")

        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        source = payload["source"].splitlines()
        assert (comment["line_start"], comment["line_end"]) == (7, 7)
        assert source[comment["line_start"] - 1] == "para two", (
            "the reported line must hold the text the comment was written about"
        )

    def test_repeated_committed_moves_do_not_compound(
        self, git_client, git_source_dir
    ):
        from server import build_view_payload

        created = self._post(git_client, git_source_dir, 5, 5)
        doc = Path(git_source_dir) / "doc.md"
        body = GIT_DOC
        for n in range(3):
            body = "preamble %d\n\n" % n + body
            doc.write_text(body)
            _git(git_source_dir, "add", "doc.md")
            _git(git_source_dir, "commit", "-qm", "move %d" % n)
            build_view_payload("doc.md")

        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        source = payload["source"].splitlines()
        assert comment["line_end"] <= len(source), "the range ran past the file"
        assert source[comment["line_start"] - 1] == "para two"

    def test_the_block_step_does_not_override_a_correct_blame_answer(
        self, git_client, git_source_dir
    ):
        """A committed blank-run collapse: blame gets it right, #465 must agree.

        The whole failure mode is the block step asserting authority over a
        position it computed less accurately than the mechanism it overrode.
        Here blame followed the real text through a real commit and landed on
        'b = 2'; a raw in-block offset pointed at the unrelated tail paragraph
        instead — outside the very block the comment is grouped under, and not
        flagged detached.  That was a regression against master, which reports
        the blame answer because it has no block step at all.
        """
        from server import build_view_payload

        doc = Path(git_source_dir) / "doc.md"
        roomy = "# T\n\n```py\na = 1\n\n\n\n\nb = 2\n```\n\nunrelated tail\n"
        doc.write_text(roomy)
        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "a roomy fence")

        created = self._post(git_client, git_source_dir, 9, 9)  # 'b = 2'

        doc.write_text("# T\n\n```py\na = 1\n\nb = 2\n```\n\nunrelated tail\n")
        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "squeeze the blank run")

        payload = build_view_payload("doc.md")
        comment = _find(payload, created["id"])
        source = payload["source"].splitlines()
        assert source[comment["line_start"] - 1] == "b = 2", (
            "the block step overrode the line blame had already got right"
        )
        assert _group_of(payload, created["id"]) == 3
        assert comment["line_start"] <= 7, "reported outside its own group"


class TestBackfillOnDirtyTree:
    """Backfill must not cement an anchor the blame migration has not corrected.

    On a dirty tracked file `_migrate_comment_anchors()` deliberately does
    nothing, so a legacy row's stored line is whatever it was before the
    uncommitted edit — quite possibly pointing at someone else's text.  Writing
    a block_id from that line is permanent: a block-id hit outranks blame from
    then on, so the later clean view that *could* have fixed it never gets the
    chance.  The served root is dirty for most of an editing session, which is
    the whole premise of #465.
    """

    def _legacy_row(self, git_source_dir, *, line_start, line_end, anchor_commit=None):
        """A row with no block_id: pre-#465, optionally #406-era (anchored)."""
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        conn = get_connection(Path(git_source_dir) / "test_comments.db")
        init_db(conn)
        row = create_comment(
            conn,
            file_id=derive_file_id(str(Path(git_source_dir) / "doc.md")),
            line_start=line_start,
            line_end=line_end,
            author="legacy",
            body="pre-#465 comment",
            file_path="doc.md",
            anchor_commit=anchor_commit,
        )
        conn.close()
        assert row["block_id"] is None
        return row

    def _stored(self, git_source_dir, comment_id):
        from db import get_comment, get_connection

        conn = get_connection(Path(git_source_dir) / "test_comments.db")
        stored = get_comment(conn, comment_id)
        conn.close()
        return stored

    def test_dirty_tree_does_not_cement_a_drifted_anchor(
        self, git_client, git_source_dir
    ):
        from server import build_view_payload

        # Stored on 'para two' (L5).  An *uncommitted* insert pushes every
        # paragraph down two lines, so L5 now holds 'para one' instead.
        row = self._legacy_row(git_source_dir, line_start=5, line_end=5)
        doc = Path(git_source_dir) / "doc.md"
        doc.write_text("preamble\n\n" + GIT_DOC)

        payload = build_view_payload("doc.md")
        assert _find(payload, row["id"]) is not None, "the comment must stay visible"
        assert self._stored(git_source_dir, row["id"])["block_id"] is None, (
            "a dirty tree gives no evidence the stored line is still right, so "
            "the block id learned from it must not be written"
        )

    def test_the_later_clean_view_can_still_heal_an_anchored_row(
        self, git_client, git_source_dir
    ):
        """The point of not cementing: blame gets its turn once work lands.

        Uses a #406-era row (``anchor_commit`` set, ``block_id`` NULL), because
        that is the only row blame will touch — one with no anchor commit is
        never migrated, so nothing could heal it on any view.  Cementing during
        the dirty window is what would rob this row of its correction.
        """
        from server import build_view_payload

        head0 = _git(git_source_dir, "rev-parse", "HEAD")
        row = self._legacy_row(
            git_source_dir, line_start=5, line_end=5, anchor_commit=head0
        )
        doc = Path(git_source_dir) / "doc.md"
        moved = "preamble\n\n" + GIT_DOC  # 'para two' L5 -> L7
        doc.write_text(moved)
        build_view_payload("doc.md")  # dirty view — must not cement
        assert self._stored(git_source_dir, row["id"])["block_id"] is None

        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "insert a preamble")
        build_view_payload("doc.md")

        stored = self._stored(git_source_dir, row["id"])
        assert stored["line_start"] == 7, "blame should have relocated the anchor"
        assert stored["block_id"] == _block_id_of(moved, 7), (
            "on a clean tree the row should heal onto 'para two' at its new line"
        )
        assert stored["block_offset"] == 0

    def test_clean_tree_still_backfills(self, git_client, git_source_dir):
        """The gate is about missing evidence, not about git roots."""
        from server import build_view_payload

        row = self._legacy_row(git_source_dir, line_start=5, line_end=5)
        build_view_payload("doc.md")
        stored = self._stored(git_source_dir, row["id"])
        assert stored["block_id"] == _block_id_of(GIT_DOC, 5)
        assert stored["block_offset"] == 0

    def test_the_dirty_check_is_skipped_when_no_row_needs_backfilling(
        self, git_client, git_source_dir, monkeypatch
    ):
        """The gate shells out to git, so it must not run for nothing.

        It exists to decide whether a *missing* block id may be written.  Every
        view of a file whose comments are already anchored — the steady state —
        was paying two subprocesses to answer a question it never asked.
        """
        import server
        from server import build_view_payload

        calls = []
        real = server._is_tracked_and_dirty
        monkeypatch.setattr(
            server,
            "_is_tracked_and_dirty",
            lambda target: (calls.append(target), real(target))[1],
        )

        _post_comment(git_client, git_source_dir, 5, 5, "anchored on create")
        build_view_payload("doc.md")
        assert calls == [], "no row was missing an id, so nothing had to be decided"

        # Two legacy rows, so "answered once and cached" is distinguishable from
        # "answered per row" — with one row the two are the same number.
        self._legacy_row(git_source_dir, line_start=3, line_end=3)
        self._legacy_row(git_source_dir, line_start=5, line_end=5)
        build_view_payload("doc.md")
        assert len(calls) == 1, "the gate is per view, not per row"

    def test_backfill_keeps_the_offset_the_legacy_row_sits_at(
        self, git_client, git_source_dir
    ):
        """A legacy row part-way into a block must not snap to the block start.

        Every other backfill fixture puts the comment on its block's own first
        line, where `line_start - block.start_line` and a hard-coded 0 are the
        same number — so nothing distinguishes the formula from the constant.
        They are not the same for a comment about the middle of a paragraph, and
        the mistake is permanent: once written, the id outranks blame forever.
        """
        from server import build_view_payload

        doc = Path(git_source_dir) / "doc.md"
        multi = "# Title\n\nalpha\nbeta\ngamma\n\ntail\n"
        doc.write_text(multi)
        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "a multi-line block")

        # 'gamma' is L5, two lines into the paragraph block that starts at L3.
        row = self._legacy_row(git_source_dir, line_start=5, line_end=5)
        build_view_payload("doc.md")

        stored = self._stored(git_source_dir, row["id"])
        assert stored["block_id"] == _block_id_of(multi, 5)
        assert stored["block_offset"] == 2

        payload = build_view_payload("doc.md")
        source = payload["source"].splitlines()
        comment = _find(payload, row["id"])
        assert source[comment["line_start"] - 1] == "gamma"

    def test_a_repo_with_no_commits_does_not_block_backfill(self, tmp_path):
        """No HEAD means there is no blame migration to wait for, ever.

        The gate exists to leave room for a correction that is still coming.  A
        repo with nothing committed has no HEAD to diff or blame against, so
        holding the backfill back there would withhold it permanently.
        """
        from server import build_view_payload, configure

        (tmp_path / "doc.md").write_text(GIT_DOC)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")
        _git(tmp_path, "add", "doc.md")  # tracked, but never committed

        configure(str(tmp_path), tmp_path / "test_comments.db")
        row = self._legacy_row(tmp_path, line_start=5, line_end=5)
        build_view_payload("doc.md")

        assert self._stored(tmp_path, row["id"])["block_id"] == _block_id_of(GIT_DOC, 5)

    def test_a_dirty_file_named_like_an_option_still_blocks_backfill(self, tmp_path):
        """The consequence of the gate misreading an option-shaped filename.

        `_is_tracked_and_dirty()` decides this, and it answers by shelling out to
        `git ls-files` with the filename as a pathspec.  For a name git takes for
        an unknown option that call fails, which reads as "not tracked" — so the
        served file looks like a non-git file, the gate opens, and the drifted
        line gets cemented into a block id.  The row is then anchored to text its
        author never commented on, permanently: a block-id hit outranks blame from
        then on, so the clean view that could have fixed it never gets its turn.

        Uses a whole repo of its own rather than `git_source_dir`, because the
        filename is the variable under test.
        """
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id
        from server import build_view_payload, configure

        name = "--foo.md"
        doc = tmp_path / name
        doc.write_text(GIT_DOC)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "test@example.com")
        _git(tmp_path, "config", "user.name", "Test")
        _git(tmp_path, "add", "--", name)
        _git(tmp_path, "commit", "-qm", "commit an option-shaped filename")

        configure(str(tmp_path), tmp_path / "test_comments.db")
        conn = get_connection(tmp_path / "test_comments.db")
        init_db(conn)
        row = create_comment(
            conn,
            file_id=derive_file_id(str(doc)),
            line_start=5,
            line_end=5,
            author="legacy",
            body="pre-#465 comment",
            file_path=name,
        )
        conn.close()
        assert row["block_id"] is None, "the fixture must be a pre-#465 row"

        # Uncommitted: 'para two' moves L5 -> L7, so the stored L5 now names
        # 'para one' — the drift that must not be turned into a permanent id.
        doc.write_text("preamble\n\n" + GIT_DOC)

        payload = build_view_payload(name)

        assert _find(payload, row["id"]) is not None, "the comment must stay visible"
        assert self._stored(tmp_path, row["id"])["block_id"] is None, (
            "a dirty tracked file gives no evidence the stored line is still "
            "right, whatever the file is called"
        )


# ── Same-block reply threading (#465, Phase 2) ───────────────────────────


def _post_form_comment(
    client, *, path="doc.md", line_start, line_end=None, body="hi", parent_id=None
):
    """Post through the web form route, the one the browser uses."""
    data = {
        "file_id": "fid",
        "path": path,
        "line_start": str(line_start),
        "line_end": str(line_end if line_end is not None else line_start),
        "author": "tester",
        "body": body,
    }
    if parent_id is not None:
        data["parent_id"] = str(parent_id)
    resp = client.post("/comment", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    return None


def _newest_row(db_path):
    from db import get_connection

    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT * FROM comments ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _insert_row(anchored, *, body, block_id, parent_id=None, path="doc.md", line=5):
    """Write a row straight to the DB, bypassing the create routes.

    Needed to build shapes the routes will no longer produce — a *second* root
    on a block that already has one, or a reply whose parent belongs to another
    file — so the read path can be tested against them.
    """
    from db import create_comment, get_connection, init_db
    from file_id import derive_file_id

    conn = get_connection(anchored["db"])
    init_db(conn)
    try:
        return create_comment(
            conn,
            file_id=derive_file_id(str(anchored["dir"] / path)),
            line_start=line,
            line_end=line,
            author="tester",
            body=body,
            parent_id=parent_id,
            file_path=path,
            block_id=block_id,
            block_offset=0,
        )
    finally:
        conn.close()


class TestSameBlockThreading:
    """A comment on a block that already has comments replies to the latest one.

    Chaos's rule, from #465: "when there is already a comment on a certain
    block, all new comments on that same block should be treated as reply to
    the previous reply or the first comment of that block."  The block is
    identified by its Phase 1 ``block_id``, never by a line range.
    """

    def test_first_comment_on_a_block_is_a_root(self, anchored):
        c1 = _post_json_comment(anchored["client"], line_start=5, body="one")
        assert c1["parent_id"] is None

    def test_second_comment_replies_to_the_first(self, anchored):
        c1 = _post_json_comment(anchored["client"], line_start=5, body="one")
        c2 = _post_json_comment(anchored["client"], line_start=5, body="two")
        assert c2["parent_id"] == c1["id"]

    def test_third_comment_replies_to_the_second_not_the_first(self, anchored):
        """"the previous reply", not "the first comment of that block"."""
        c1 = _post_json_comment(anchored["client"], line_start=5, body="one")
        c2 = _post_json_comment(anchored["client"], line_start=5, body="two")
        c3 = _post_json_comment(anchored["client"], line_start=5, body="three")
        assert c3["parent_id"] == c2["id"], (
            f"expected the latest comment {c2['id']} ('two'), "
            f"got {c3['parent_id']} (first is {c1['id']})"
        )

    def test_a_comment_on_another_block_of_the_same_file_is_a_root(self, anchored):
        _post_json_comment(anchored["client"], line_start=5, body="on target")
        other = _post_json_comment(anchored["client"], line_start=3, body="on first")
        assert other["parent_id"] is None

    def test_threading_follows_the_block_not_the_line(self, anchored):
        """Two blocks swap places; the thread moves with the text, not the line.

        A line-keyed implementation gives the exact opposite answer to both
        halves of this, which is why Phase 1 had to land first.
        """
        c1 = _post_json_comment(anchored["client"], line_start=5, body="on target")
        # "target paragraph" moves from L5 to L3; "first paragraph" takes L5.
        anchored["doc"].write_text(
            "# Title\n\ntarget paragraph\n\nfirst paragraph\n\nlast paragraph\n"
        )

        moved = _post_json_comment(
            anchored["client"], line_start=3, body="on target, now at L3"
        )
        assert moved["parent_id"] == c1["id"], (
            "a comment on the block that was commented on must reply to that "
            "comment wherever the block has moved to"
        )

        squatter = _post_json_comment(
            anchored["client"], line_start=5, body="on first, now at L5"
        )
        assert squatter["parent_id"] is None, (
            "a different block that happens to occupy the commented line is "
            "not the commented block"
        )

    def test_an_explicit_parent_id_is_honoured(self, anchored):
        """A caller that names a parent means it; derivation only fills a gap."""
        c1 = _post_json_comment(anchored["client"], line_start=5, body="one")
        c2 = _post_json_comment(anchored["client"], line_start=5, body="two")
        resp = anchored["client"].post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "deliberate reply to the root",
                "parent_id": c1["id"],
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["parent_id"] == c1["id"], (
            f"explicit parent {c1['id']} must survive; latest was {c2['id']}"
        )

    def test_the_web_form_route_threads_too(self, anchored):
        _post_form_comment(anchored["client"], line_start=5, body="one")
        first = _newest_row(anchored["db"])
        _post_form_comment(anchored["client"], line_start=5, body="two")
        second = _newest_row(anchored["db"])
        assert second["body"] == "two"
        assert second["parent_id"] == first["id"]

    def test_the_form_no_longer_hardcodes_a_zero_parent(self, anchored):
        """The hidden field is what made every web comment a root (#465)."""
        html = anchored["client"].get("/view?path=doc.md").text
        assert 'name="parent_id" value="0"' not in html

    def test_a_line_in_no_block_does_not_thread(self, anchored):
        """No block identity, no thread: L2 is the blank line between blocks."""
        c1 = _post_json_comment(anchored["client"], line_start=2, body="in the gap")
        c2 = _post_json_comment(anchored["client"], line_start=2, body="also there")
        assert c1["block_id"] is None
        assert c2["parent_id"] is None

    def test_a_comment_on_the_same_text_in_another_file_is_a_root(self, anchored):
        """Block ids are content-derived, so two files can share one."""
        (anchored["dir"] / "twin.md").write_text(DOC_465)
        c1 = _post_json_comment(anchored["client"], line_start=5, body="on doc")
        twin = _post_json_comment(
            anchored["client"], path="twin.md", line_start=5, body="on twin"
        )
        assert c1["block_id"] == twin["block_id"], "fixture assumption: same text"
        assert twin["parent_id"] is None


def _comment_count(db_path):
    from db import get_connection

    conn = get_connection(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
    finally:
        conn.close()


def _rekey_comment(db_path, old_id, new_id):
    """Move an existing comment to *new_id*, so a real row sits at a chosen id.

    Re-keys a comment the create route already made rather than inserting a
    hand-built row, so the row keeps whatever ``block_id``/``file_path``/anchor
    the server derived for it.  The point under test is the *id*; a synthetic
    row would drag the test into duplicating derivation logic and could pass
    while the real thing was unreachable.

    Only ids the app can never mint itself are worth reaching for this way --
    ``comments.id`` is ``AUTOINCREMENT``, so the boundary values of a SQLite
    INTEGER are otherwise unobservable from the API (see
    ``TestSqliteIntegerBound``).  Re-keying leaves ``sqlite_sequence`` alone, so
    subsequent inserts still get their ordinary ids.
    """
    from db import get_connection

    conn = get_connection(db_path)
    try:
        cur = conn.execute(
            "UPDATE comments SET id = ? WHERE id = ?", (new_id, old_id)
        )
        assert cur.rowcount == 1, (
            f"fixture: no comment with id {old_id} to move to {new_id} -- without "
            "this check a helper that quietly matched no row would leave the "
            "caller's id naming nothing, and the resulting 422 would read as a "
            "defect in the bound under test rather than a broken fixture"
        )
        conn.commit()
    finally:
        conn.close()


class TestUnknownExplicitParent:
    """Naming a parent that does not exist is a client error, not a 500 (#470).

    ``comments.parent_id`` is a foreign key and ``db.py`` sets
    ``PRAGMA foreign_keys=ON``, so an explicit id that resolves to no row used
    to reach the insert and raise an unhandled ``sqlite3.IntegrityError``.  A
    500 tells the CLI nothing: it cannot distinguish "you named a comment that
    isn't there" from "the server is broken".
    """

    def test_an_unknown_explicit_parent_is_rejected_not_a_server_error(self, anchored):
        resp = anchored["client"].post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "reply to nobody",
                "parent_id": 99999,
            },
        )
        assert resp.status_code == 422, (
            "an id naming no comment is the caller's mistake, so it must come "
            f"back 4xx, not 5xx and not a stored row; got {resp.status_code}"
        )

    def test_the_rejection_names_the_id_that_was_not_found(self, anchored):
        """A bare 422 is no better than a 500 for telling the CLI what to fix."""
        resp = anchored["client"].post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "reply to nobody",
                "parent_id": 99999,
            },
        )
        assert "99999" in str(resp.json().get("detail", "")), (
            "the message must name the missing id so the caller can tell which "
            f"parent was wrong; got {resp.json()!r}"
        )

    def test_a_rejected_parent_stores_nothing(self, anchored):
        """The insert must not half-happen: rejected in, nothing written."""
        _post_json_comment(anchored["client"], line_start=5, body="a real root")
        before = _comment_count(anchored["db"])
        anchored["client"].post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "reply to nobody",
                "parent_id": 99999,
            },
        )
        assert _comment_count(anchored["db"]) == before, (
            "a rejected create must leave the table exactly as it found it"
        )

    def test_the_form_route_rejects_an_unknown_parent_too(self, anchored):
        """Both create routes share the derivation, so both must be fixed."""
        resp = anchored["client"].post(
            "/comment",
            data={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": "5",
                "line_end": "5",
                "author": "tester",
                "body": "reply to nobody",
                "parent_id": "99999",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 422, (
            "the form route derives its parent through the same function, so an "
            f"unknown id must not 500 there either; got {resp.status_code}"
        )

    def test_an_explicit_parent_of_zero_still_means_no_parent(self, anchored):
        """``parent_id=0`` is the old form's "none", not a lookup of row 0.

        Pinned here because the validation added for #470 sits on exactly the
        guard that turns 0 into "derive instead" -- without this, tightening
        that guard would send 0 to the comments table and 4xx every legacy
        caller that still posts it.
        """
        c1 = _post_json_comment(anchored["client"], line_start=5, body="one")
        resp = anchored["client"].post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "zero means none",
                "parent_id": 0,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["parent_id"] == c1["id"], (
            "an explicit 0 must fall through to derivation and chain onto the "
            f"block's latest comment {c1['id']}, not be looked up as an id"
        )


class TestOutOfRangeExplicitParent:
    """An id outside the range of real ids is the same mistake as an unknown one (#473).

    #470 closed the case of a *plausible* id naming no row.  Two neighbours of it
    were left open, and they are worse than the 500 that issue fixed:

    - A negative id fell through the ``explicit > 0`` guard into *derivation*, so
      the create returned **201 with a different parent than the caller named** --
      a silent wrong answer rather than an error.
    - An id too large for a SQLite INTEGER raised ``OverflowError`` inside the
      lookup, so it was still a 500.

    The line held here: ``0`` and absent still mean "no parent" and derive (legacy
    callers depend on it, pinned by
    ``test_an_explicit_parent_of_zero_still_means_no_parent``).  Any other value is
    a request to reply to one specific comment, and is the caller's mistake if no
    such comment exists.
    """

    def _post(self, client, parent_id):
        return client.post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": 5,
                "line_end": 5,
                "author": "tester",
                "body": "reply to an impossible id",
                "parent_id": parent_id,
            },
        )

    def test_a_negative_parent_is_rejected_not_silently_rewritten(self, anchored):
        """The caller named -5; answering as if they had named something else is worse than an error."""
        root = _post_json_comment(anchored["client"], line_start=5, body="a real root")
        resp = self._post(anchored["client"], -5)
        assert resp.status_code == 422, (
            "a negative id names no comment, so it is the same client mistake as "
            "an unknown one; instead the create succeeded and quietly reparented "
            f"onto {root['id']} -- got {resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )

    def test_the_negative_rejection_names_the_id_that_was_asked_for(self, anchored):
        """Echo back what the caller sent, not a sanitised or derived value."""
        resp = self._post(anchored["client"], -5)
        assert "-5" in str(resp.json().get("detail", "")), (
            "the message must name the id the caller actually sent so they can "
            f"tell which parent was wrong; got {resp.json()!r}"
        )

    def test_a_negative_parent_stores_nothing(self, anchored):
        """Rejected in, nothing written -- and in particular no re-rooted reply."""
        _post_json_comment(anchored["client"], line_start=5, body="a real root")
        before = _comment_count(anchored["db"])
        self._post(anchored["client"], -5)
        assert _comment_count(anchored["db"]) == before, (
            "a rejected create must leave the table exactly as it found it; a row "
            "here means the caller got a reply threaded under a parent they never named"
        )

    def test_an_out_of_range_parent_is_rejected_not_a_server_error(self, anchored):
        """``10**19`` does not fit a SQLite INTEGER, which is still a client error."""
        resp = self._post(anchored["client"], 10**19)
        assert resp.status_code == 422, (
            "an id too large to be any row's id is the caller's mistake, but it "
            "reached the lookup and raised OverflowError, surfacing as a 5xx -- "
            f"the very defect #470 set out to remove; got {resp.status_code}"
        )

    def test_the_out_of_range_rejection_names_the_id(self, anchored):
        resp = self._post(anchored["client"], 10**19)
        assert str(10**19) in str(resp.json().get("detail", "")), (
            "an out-of-range id must be reported like any other missing parent, "
            f"naming the value; got {resp.json()!r}"
        )

    def test_a_hugely_negative_parent_is_rejected_too(self, anchored):
        """Below the INTEGER floor: negative *and* out of range at once.

        Pins the order of the two checks.  An implementation that rejects
        negatives only after looking them up still 500s here, and one that range
        checks only the positive end never reaches the negative rule.
        """
        resp = self._post(anchored["client"], -(10**19))
        assert resp.status_code == 422, (
            "an id below the SQLite INTEGER floor must be rejected by the same "
            f"rule, not overflow the lookup or derive a parent; got {resp.status_code}"
        )

    def test_the_form_route_rejects_an_out_of_range_parent_too(self, anchored):
        """Both create routes derive through the same function, so both are covered."""
        resp = anchored["client"].post(
            "/comment",
            data={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": "5",
                "line_end": "5",
                "author": "tester",
                "body": "reply to an impossible id",
                "parent_id": str(10**19),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 422, (
            "the form route must not 500 on an id the JSON route rejects cleanly; "
            f"got {resp.status_code}"
        )

    def test_the_boundary_value_itself_is_rejected(self, anchored):
        """``2**63`` is the *first* value the driver refuses to bind (#477 item 2).

        ``10**19`` is ~1.08x the bound, wide enough that it cannot see an
        off-by-one: with ``<= 2**63`` in the shared helper this call site went
        back to a 500 -- ``OverflowError`` inside ``get_comment`` -- while every
        test in this class stayed green, because the bound was defended only by
        a *resolve*-route test reaching it through ``_fits_sqlite_integer``.
        This pins it where the ``parent_id`` code path can see it, so the two
        call sites drifting apart cannot silently unpin this one.
        """
        resp = self._post(anchored["client"], 2**63)
        assert resp.status_code == 422, (
            "2**63 is out of range by exactly one, so it must be refused before "
            f"the lookup rather than overflow inside it; got {resp.status_code}"
        )

    def test_the_boundary_value_below_the_floor_is_rejected(self, anchored):
        """The floor's twin of the case above (#478 review, Minor 2).

        ``test_a_hugely_negative_parent_is_rejected_too`` sends ``-(10**19)``,
        which is as coarse at the bottom as ``10**19`` was at the top: widening
        the shared helper to ``-(2**63) - 1 <= value`` leaves every other test in
        this class green while this call site goes back to a 500 --
        ``OverflowError`` inside ``get_comment``.  ``-(2**63)-1`` is the first
        value below the range, so from here it is the only one that can see that
        widening, and it leaves the *rejecting* side of the bound pinned from
        both ends at both call sites rather than at the top only.
        """
        resp = self._post(anchored["client"], -(2**63) - 1)
        assert resp.status_code == 422, (
            "-(2**63)-1 is below the SQLite INTEGER floor by exactly one, so it "
            "must be refused before the lookup rather than overflow inside it; "
            f"got {resp.status_code}"
        )

    def test_a_parent_at_the_floor_is_accepted_when_it_names_a_real_row(self, anchored):
        """The *accepting* side of the floor, which #479 wrongly called unreachable.

        #479 pinned the rejecting side here and argued the accepting side could
        not be pinned at this call site at all: an in-range id naming no comment
        and an out-of-range one answer byte-identical 422s, and seeding a comment
        at the boundary to reply to is impossible because ``comments.id`` is
        ``AUTOINCREMENT``.  That argument holds at the **top** and was carried
        over to the floor unchecked (its reviewer caught it, #479 Minor 1).
        ``AUTOINCREMENT`` constrains only the *maximum*: a row at ``-(2**63)``
        inserts fine, later inserts still get their ordinary ids, and it can be
        replied to -- so ``-(2**63) < value`` (the floor made exclusive) is
        observable from here as **422 instead of 201**, and is refused by it now.

        Note the narrower claim: this closes *one* of the two mutations #479 left
        green in this class, not both.  Narrowing the **top** (``value < 2**63 -
        1``) still survives here and should, for the ``AUTOINCREMENT`` reason
        above; it stays pinned at the helper by ``TestSqliteIntegerBound``.

        The second root exists so that the two possible answers cannot coincide:
        with only the re-keyed row in the block, "honoured the explicit parent"
        and "ignored it and derived the latest comment in the block" would both
        produce ``-(2**63)`` and the id assertion would be vacuous.  Both roots
        sit in the *same block* as the reply on purpose -- whether a cross-block
        parent should be rejected is #473 item 3, still undecided, and this test
        must not quietly take a side on it.
        """
        root = _post_json_comment(anchored["client"], line_start=5, body="a real root")
        _rekey_comment(anchored["db"], root["id"], -(2**63))
        later = _post_json_comment(
            anchored["client"], line_start=5, body="the newer comment in this block"
        )

        resp = self._post(anchored["client"], -(2**63))

        assert resp.status_code == 201, (
            "-(2**63) is the lowest id SQLite will bind, and here it names a real "
            "comment, so the reply must be created; a bound that excludes the "
            "floor turns a legal parent into a client error -- got "
            f"{resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == -(2**63), (
            "the reply must hang off the comment the caller named, not off the "
            f"most recent one in the block ({later['id']}); got "
            f"{resp.json()['parent_id']}"
        )


class TestCrossBlockOrCrossFileExplicitParent:
    """A parent that exists but belongs elsewhere is refused, not silently kept (#473 item 3).

    Owner decision on this issue: "should a cross-block or cross-file explicit
    parent be rejected?  Yes."

    The reason it is worth refusing is that accepting it does not do what the
    caller asked.  #469 established that a thread is rendered per block and a
    root outside the block being rendered is not reachable, so a reply stored
    with a parent in another block (or another file) comes back as its own
    root: the caller asked to answer one specific comment, got a 201 saying it
    worked, and the reply is not a reply.  That is the same species as the
    silent resolve no-op fixed for #475 item 2 -- a success reported for
    something that did not happen.

    The line held here, and what it deliberately does *not* move:

    - "Another file" is decided by the same rule the block-group query in
      ``db.py`` uses -- ``file_path`` when the row has one, ``file_id`` when it
      does not.  A legacy row predating the ``file_path`` column matches by
      content id and is still a legal parent.
    - A block identity that is absent on either side is not a *different* block.
      A parent with no ``block_id`` (a pre-#465 row, or a comment on the blank
      line between blocks) and a reply written on no block are both still
      accepted; refusing them would be a behaviour change well past what was
      asked, and neither is provably cross-block.
    - ``0`` and absent still mean "no parent" and fall through to derivation,
      and negative/out-of-range/unknown keep the 422 they already answer.

    422 rather than 404 because every other guard on this field answers 422 and
    nothing here needs the two codes to differ.  (The resolve routes needed
    that for #475 item 2: an in-range unknown ``comment_id`` had to stay
    distinguishable from an out-of-range one.  On ``parent_id`` the accepting
    side of the bound is pinned at the floor instead -- see
    ``TestSqliteIntegerBound`` -- so folding these into 422 costs no
    observability.)
    """

    def _post(self, client, parent_id, *, path="doc.md", line_start=5):
        return client.post(
            "/api/comments",
            json={
                "file_id": "fid",
                "path": path,
                "line_start": line_start,
                "line_end": line_start,
                "author": "tester",
                "body": "a deliberate reply to a comment elsewhere",
                "parent_id": parent_id,
            },
        )

    def test_a_parent_on_another_block_of_the_same_file_is_rejected(self, anchored):
        """L3 and L5 are different paragraphs, so they are different threads."""
        elsewhere = _post_json_comment(
            anchored["client"], line_start=3, body="on the first paragraph"
        )
        resp = self._post(anchored["client"], elsewhere["id"])
        assert resp.status_code == 422, (
            "a parent on another block cannot be the root of a thread rendered "
            "on this one, so the reply would come back a root -- the caller must "
            f"be told instead of getting a 201; got {resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )

    def test_the_cross_block_rejection_names_the_id(self, anchored):
        """A bare 422 does not tell the CLI which parent to fix.

        The guard is named as well as the id (#503 item 21).  The id alone does
        rule out a silent 201 -- a 201 body carries no ``detail``, so the
        assertion cannot pass on one -- but it accepts *any* 422 that names the
        id, and the not-found guard names it too.  Measured: dropping the parent
        row after the create call below stores it leaves this test green on
        ``parent_id N does not name an existing comment``, so a regression that
        stopped storing the parent would keep the test passing while the block
        rule it is named for was never consulted.  (``anchored`` builds the
        document, the database and the client and no comments; the parent is
        this test's own create.)  ``another block`` is the block guard's message
        and no other guard's, so it is what tells them apart.

        Only that phrase is pinned, not the order of the guards (#503 item 2,
        still open): the parent here is created on this same file through the
        API, so the file guard has nothing to answer whichever check runs first.

        The status assertion earns its place in one direction only, and it is
        not the obvious one.  It does *not* catch a silent 201: a 201 body
        carries no ``detail``, so both message assertions below already fail on
        one -- and with this assertion removed it is the *id* one that fires,
        being ordered first.  What it catches is the same message under a
        different code -- mutating the block guard to answer 409 leaves
        ``another block`` in ``detail``, so both message assertions pass and
        only this one fails.  Beyond that it is the better diagnostic, failing
        first and printing what came back rather than leaving a reader to infer
        the status from an absent ``detail``.
        """
        elsewhere = _post_json_comment(
            anchored["client"], line_start=3, body="on the first paragraph"
        )
        resp = self._post(anchored["client"], elsewhere["id"])
        assert resp.status_code == 422, (
            "the message is only worth reading under the status the sibling "
            "tests pin -- a guard that kept this wording but answered a "
            f"different code would slip past the message assertion; got "
            f"{resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )
        detail = str(resp.json().get("detail", ""))
        assert str(elsewhere["id"]) in detail, (
            "the message must name the id the caller sent so they can tell which "
            f"parent was wrong; got {resp.json()!r}"
        )
        assert "another block" in detail, (
            "the 422 has to come from the *block* guard, or this test is not "
            "exercising the rule it is named for -- the not-found guard answers "
            f"422 and names the id as well; got {resp.json()!r}"
        )

    def test_a_cross_block_parent_stores_nothing(self, anchored):
        """Rejected in, nothing written -- in particular no reply that is not one."""
        elsewhere = _post_json_comment(
            anchored["client"], line_start=3, body="on the first paragraph"
        )
        before = _comment_count(anchored["db"])
        self._post(anchored["client"], elsewhere["id"])
        assert _comment_count(anchored["db"]) == before, (
            "a rejected create must leave the table as it found it; a row here is "
            "a comment stored with a parent it will never render under"
        )

    def test_the_form_route_rejects_a_cross_block_parent_too(self, anchored):
        """Both create routes derive through the same function, so both are covered."""
        elsewhere = _post_json_comment(
            anchored["client"], line_start=3, body="on the first paragraph"
        )
        resp = anchored["client"].post(
            "/comment",
            data={
                "file_id": "fid",
                "path": "doc.md",
                "line_start": "5",
                "line_end": "5",
                "author": "tester",
                "body": "a deliberate reply to a comment elsewhere",
                "parent_id": str(elsewhere["id"]),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 422, (
            "the form route must refuse what the JSON route refuses, or the "
            f"browser is the one client that can still do it; got {resp.status_code}"
        )

    def test_a_parent_on_the_same_text_in_another_file_is_rejected(self, anchored):
        """The file rule, isolated: identical text means an *identical* block_id.

        ``block_id`` is derived from block content, so a block comparison alone
        answers "same block" here and lets the cross-file parent through.  This
        is the only test in the class that a block-only implementation fails.
        """
        (anchored["dir"] / "twin.md").write_text(DOC_465)
        twin = _post_json_comment(
            anchored["client"], path="twin.md", line_start=5, body="on twin"
        )
        here = _post_json_comment(anchored["client"], line_start=5, body="on doc")
        assert twin["block_id"] == here["block_id"], "fixture assumption: same text"

        resp = self._post(anchored["client"], twin["id"])
        assert resp.status_code == 422, (
            "the parent is in another file, so it is not in the thread this reply "
            "will be rendered in, however identical the two blocks are; got "
            f"{resp.status_code} {resp.json() if resp.status_code == 201 else resp.text}"
        )

    def test_a_legacy_parent_with_no_file_path_is_still_honoured(self, anchored):
        """Rows predating the ``file_path`` column match by ``file_id``, as elsewhere.

        ``latest_comment_in_block()`` scopes a block's thread with
        ``file_path = ? OR (file_path IS NULL AND file_id = ?)``.  A guard that
        compares ``file_path`` alone reads such a row as cross-file and starts
        rejecting replies to it -- a legal parent turned into a client error by
        a column that was added later.
        """
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        conn = get_connection(anchored["db"])
        init_db(conn)
        try:
            legacy = create_comment(
                conn,
                file_id=derive_file_id(str(anchored["doc"])),
                line_start=5,
                line_end=5,
                author="tester",
                body="written before file_path existed",
                file_path=None,
                block_id=_block_id_of(DOC_465, 5),
                block_offset=0,
            )
        finally:
            conn.close()

        resp = self._post(anchored["client"], legacy["id"])
        assert resp.status_code == 201, (
            "a row with no file_path belongs to this file by content id, which is "
            f"how the thread query already scopes it; got {resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == legacy["id"], (
            "the reply must hang off the parent the caller named"
        )

    def test_a_legacy_parent_whose_file_id_names_another_file_is_rejected(
        self, anchored
    ):
        """The other half of the legacy rule: a content id can also *fail* to match.

        ``test_a_legacy_parent_with_no_file_path_is_still_honoured`` pins the
        accepting half only, and an implementation that read "no ``file_path``"
        as "belongs to whatever file is asking" passes it while admitting this
        row.  Nothing downstream catches that: the two files hold identical
        text, so the parent's ``block_id`` matches a block of *this* document
        and the block comparison answers "same block".  The ``file_id``
        comparison is the only thing between this create and a 201 whose parent
        is not in the document being read -- and the reply, having no reachable
        root here, comes back a root itself.

        Built from the rule rather than by copying the accept test's fixture:
        the parent has to name a different file whose block text is identical,
        or a block-only implementation passes it for the wrong reason.

        The message is asserted on because a 422 alone cannot say *which* guard
        answered (#503 item 17).  The not-found guard names the id too, so with
        the id as the only claim about the body this test passes green on a
        fixture that never stored the parent at all -- measured: dropping the
        row leaves ``parent_id N does not name an existing comment`` and the
        assertion accepts it, so a regression that broke the fixture would
        retire the coverage silently rather than fail.  Only the phrase is
        pinned, not the order of the guards (#503 item 2, still open): the
        twin's block is byte-identical and the reply is written on that block,
        so the block guard cannot fire here whichever check runs first.
        """
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        twin = anchored["dir"] / "twin.md"
        twin.write_text(DOC_465)
        conn = get_connection(anchored["db"])
        init_db(conn)
        try:
            legacy = create_comment(
                conn,
                file_id=derive_file_id(str(twin)),
                line_start=5,
                line_end=5,
                author="tester",
                body="written before file_path existed, and not on this file",
                file_path=None,
                block_id=_block_id_of(DOC_465, 5),
                block_offset=0,
            )
        finally:
            conn.close()

        assert legacy["block_id"] == _block_id_of(DOC_465, 5), (
            "fixture assumption: the twin's block is byte-identical to this "
            "file's, so the block comparison cannot be what refuses this"
        )
        listed = anchored["client"].get("/api/comments", params={"path": "doc.md"})
        assert all(c["id"] != legacy["id"] for c in listed.json()), (
            "fixture assumption: the row is scoped to twin.md by content id, so "
            "a reader of doc.md never sees it -- which is why a reply naming it "
            "would come back a root rather than a reply"
        )

        resp = self._post(anchored["client"], legacy["id"])
        assert resp.status_code == 422, (
            "the parent's content id names twin.md, so it is a comment on another "
            "file however identical the two blocks are; got "
            f"{resp.status_code} {resp.json() if resp.status_code == 201 else resp.text}"
        )
        detail = str(resp.json().get("detail", ""))
        assert str(legacy["id"]) in detail, (
            f"the message must name the id the caller sent; got {resp.json()!r}"
        )
        assert "another file" in detail, (
            "the 422 has to come from the *file* guard, or this test is not "
            "exercising the rule it is named for -- the not-found guard answers "
            f"422 and names the id as well; got {resp.json()!r}"
        )

    def test_a_legacy_parent_survives_a_target_the_server_cannot_read(self, anchored):
        """An underivable file id is not evidence of another file (#503 item 3).

        The legacy arm of the file check asks ``derive_file_id()`` what this
        document's content id is, and answering that reads the file: an
        untracked target falls through to ``content_hash_id()``, which hashes
        the bytes.  A target the server cannot read raises ``PermissionError``
        out of the guard and the create answers **500** -- the caller is handed
        a server fault over a condition it cannot act on, which is exactly the
        class of bug #470, #472 and #475 item 2 removed from this field, and a
        request base ``c22217e`` answered 201 before the guard existed.

        The fix has to accept, not refuse.  An unreadable file leaves the guard
        with no evidence in *either* direction, and "names a comment on another
        file" is a positive claim about the document -- made here on the
        strength of a permission bit.  Accepting is also what every neighbour on
        this path already does with the same file: ``_block_anchor_for_new_comment()``
        answers ``(None, None, None)`` and ``_rendered_block_of()`` answers
        ``None``, both meaning "no placement to compare against, so nothing to
        refuse on".

        Only the legacy arm reaches it.  A parent carrying a ``file_path``
        settles the file by string comparison, and the derivation arm
        (``latest_comment_in_block()``) is unreachable here -- with the file
        unreadable the reply has no ``block_id`` of its own, so
        ``_parent_for_new_comment()`` has already returned.
        """
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        conn = get_connection(anchored["db"])
        init_db(conn)
        try:
            legacy = create_comment(
                conn,
                file_id=derive_file_id(str(anchored["doc"])),
                line_start=5,
                line_end=5,
                author="tester",
                body="written before file_path existed",
                file_path=None,
                block_id=_block_id_of(DOC_465, 5),
                block_offset=0,
            )
        finally:
            conn.close()

        anchored["doc"].chmod(0o000)
        try:
            try:
                anchored["doc"].read_bytes()
            except OSError:
                pass
            else:
                pytest.skip(
                    "this process can read a 0o000 file (running as root?), so the "
                    "condition under test cannot be set up here"
                )
            resp = self._post(anchored["client"], legacy["id"])
        finally:
            anchored["doc"].chmod(0o644)

        assert resp.status_code == 201, (
            "a file the server cannot read tells the guard nothing about which "
            "file the parent belongs to, and an unreadable target is not a client "
            f"error to report as one; got {resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == legacy["id"], (
            "the reply must still hang off the parent the caller named"
        )

    def test_an_unreadable_target_does_not_excuse_a_parent_with_a_file_path(
        self, anchored
    ):
        """The fail-open reaches legacy rows only (#503 item 15).

        ``_may_be_content_id_of()`` answers ``True`` for a target it cannot
        read, and that answer is tolerable only because the conjunct in front of
        it -- ``parent["file_path"] is None`` -- keeps it away from rows that
        settle the question by string comparison and never needed the file read
        at all.  Consult the predicate for *every* parent instead and a row
        stored on ``twin.md`` is accepted for a comment on ``doc.md`` for as
        long as ``doc.md`` is unreadable: a 201 whose reply comes back a root,
        bought by a transient permission bit on a file neither comment's
        identity depends on.

        What is pinned here is the *reach* of the fail-open rather than its
        answer, which is the half its two neighbours leave open.
        ``..._whose_file_id_names_another_file_is_rejected`` holds the target
        readable, so a widened predicate still derives a real content id and
        still refuses.  ``..._survives_a_target_the_server_cannot_read`` holds
        the target unreadable but names a ``file_path IS NULL`` parent, which
        the conjunct admits either way.  Non-legacy parent *and* unreadable
        target is the one cell the conjunct decides by itself, so it is the only
        one that can say which rows may reach the fail-open.

        Built from the rule, not from the neighbour's fixture: the parent
        carries a real ``file_path`` *and* a content id that genuinely names the
        other file, so an implementation that refused it by digest rather than
        by string would pass for a reason this test is not making a claim about.

        The message is pinned to the file guard's phrasing for the reason given
        on ``..._whose_file_id_names_another_file_is_rejected`` (#503 item 17):
        the not-found guard answers 422 and names the id too, so the id alone
        cannot tell the two apart, and a fixture that stopped storing the parent
        would leave this test green while the fail-open it exists to bound was
        never consulted.
        """
        from db import create_comment, get_connection, init_db
        from file_id import derive_file_id

        twin = anchored["dir"] / "twin.md"
        twin.write_text(DOC_465)
        conn = get_connection(anchored["db"])
        init_db(conn)
        try:
            foreign = create_comment(
                conn,
                file_id=derive_file_id(str(twin)),
                line_start=5,
                line_end=5,
                author="tester",
                body="on the twin, and it says so",
                file_path="twin.md",
                block_id=_block_id_of(DOC_465, 5),
                block_offset=0,
            )
        finally:
            conn.close()

        assert foreign["file_path"] == "twin.md", (
            "fixture assumption: this row carries a file_path, so it is *not* "
            "the legacy shape the fail-open exists for -- that is the whole of "
            "what this test distinguishes"
        )

        anchored["doc"].chmod(0o000)
        try:
            try:
                anchored["doc"].read_bytes()
            except OSError:
                pass
            else:
                pytest.skip(
                    "this process can read a 0o000 file (running as root?), so the "
                    "condition under test cannot be set up here"
                )
            resp = self._post(anchored["client"], foreign["id"])
        finally:
            anchored["doc"].chmod(0o644)

        assert resp.status_code == 422, (
            "the parent says which file it is on, so an unreadable target is no "
            "reason to stop believing it -- the fail-open is for rows that "
            "cannot answer, not for rows whose answer is inconvenient to check; "
            f"got {resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )
        detail = str(resp.json().get("detail", ""))
        assert str(foreign["id"]) in detail, (
            f"the message must name the id the caller sent; got {resp.json()!r}"
        )
        assert "another file" in detail, (
            "the 422 has to come from the *file* guard, or the fail-open's reach "
            "is not what was measured -- the not-found guard answers 422 and "
            f"names the id as well; got {resp.json()!r}"
        )

    def test_a_parent_with_no_block_id_is_still_honoured(self, anchored):
        """No block identity on the parent is not a *different* block.

        Pre-#465 rows carry no ``block_id``.  Refusing every reply to one would
        be a behaviour change well beyond the question that was asked, and the
        row is not provably elsewhere -- absent is not different.

        The row sits at L3, which is *another* block, so this pins the choice
        rather than dodging it: the read path would backfill the row onto the
        L3 block, and a guard that placed a ``block_id``-less parent by its line
        would refuse this create.  It does not, deliberately -- refusing on an
        identity the server itself invented from a line number is the mistake
        Phase 1 exists to avoid (see ``_parent_for_new_comment``).  Whether that
        is the right answer is the open question tracked on #503; what is pinned
        here is the answer we ship, not a preference for it.
        """
        legacy = _insert_row(anchored, body="pre-#465 row", block_id=None, line=3)

        resp = self._post(anchored["client"], legacy["id"])
        assert resp.status_code == 201, (
            "a parent with no block_id is not in another block, it is in no "
            f"block; got {resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == legacy["id"], (
            "the explicit parent must survive -- derivation would answer None "
            "here, since the row carries no block_id to be the latest of"
        )

    def test_a_reply_to_a_detached_parent_is_still_created(self, anchored):
        """A stored ``block_id`` the document no longer holds is not "another block".

        The guard compares block identity, but the read path does not thread by
        the *stored* id: a row whose block is gone is re-placed by line, which
        is what #468/#496/#497 made first-class.  The two disagree exactly when
        the parent has detached — somebody edited the paragraph it sits on —
        and there the stored ids differ while the rendered placement does not.

        So this reply is not the mistake the guard exists to catch: it comes
        back a *reply*, indented under the comment it answers, in the block the
        reader can see them both on.  Asserted through the render, not just the
        201, because the render is the claim — a 422 here would refuse a create
        that works, and its message would name "another block" while pointing
        at the block the parent is displayed on.  `comments_cli post
        --parent-id` posts over this route against a listing that tags such a
        comment `[DETACHED]` (#497), so it is an everyday request.
        """
        import server

        root = _post_json_comment(anchored["client"], line_start=5, body="a real root")
        anchored["doc"].write_text(
            "# Title\n\nfirst paragraph\n\ntarget paragraph, revised\n\nlast paragraph\n"
        )

        resp = self._post(anchored["client"], root["id"])
        assert resp.status_code == 201, (
            "the parent's text is gone, so its stored block_id names no block and "
            "cannot name a *different* one; the reply threads onto it where it is "
            f"rendered; got {resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == root["id"], (
            "the explicit parent must survive — derivation would answer None, the "
            "detached root not being the latest comment of the reply's new block"
        )

        payload = server.build_view_payload("doc.md")
        reply = _find(payload, resp.json()["id"])
        assert _group_of(payload, root["id"]) == _group_of(payload, reply["id"]), (
            "fixture assumption: the detached root is re-placed onto the block "
            "the reply was written on, or this test is not about threading"
        )
        assert reply["depth"] == 1, (
            "the create that would have been refused produces a properly nested "
            f"reply, which is exactly what the caller asked for; got depth "
            f"{reply['depth']}"
        )

    def test_a_detached_parent_shown_on_another_block_is_rejected(self, anchored):
        """The other half of detachment: re-placed *elsewhere* is still elsewhere.

        ``test_a_reply_to_a_detached_parent_is_still_created`` above pins the
        case where the fallback lands on the reply's own block.  It is only
        half the space.  A detached row is not unplaced — the read path groups
        it under the block its line falls in and flags it ``detached``
        (#468/#496/#497) — so when that group is *another* block, a reply
        naming it is the very create this guard exists to refuse: it comes back
        at ``depth 0`` in a different group, which is not a reply.

        Treating "no matching block" as "no opinion" accepts it, because the
        matcher stops one step short of the placement the reader is shown.  The
        two halves have to be decided by where the parent is *displayed*, which
        is the only rule that answers both the same way the view does.
        """
        import server

        root = _post_json_comment(
            anchored["client"], line_start=3, body="on the first paragraph"
        )
        # Edit the paragraph the root sits on: its stored block_id now names no
        # block, while its line still lands on the block that replaced it.
        anchored["doc"].write_text(
            "# Title\n\nfirst paragraph, revised\n\ntarget paragraph\n\nlast paragraph\n"
        )

        payload = server.build_view_payload("doc.md")
        assert _find(payload, root["id"])["detached"] is True, (
            "fixture assumption: the root must have lost its text, or this test "
            "is not about a detached parent"
        )
        assert _group_of(payload, root["id"]) == 3, (
            "fixture assumption: the detached root is still shown, grouped on the "
            "block at its line (L3) — which is not the block the reply is on (L5)"
        )

        resp = self._post(anchored["client"], root["id"])
        assert resp.status_code == 422, (
            "the parent is displayed on the L3 block and this reply is written on "
            "the L5 one, so the reply would come back a root at depth 0 in another "
            "group — the 201-that-is-not-a-reply this guard exists to refuse; got "
            f"{resp.status_code} {resp.json() if resp.status_code == 201 else resp.text}"
        )
        assert str(root["id"]) in str(resp.json().get("detail", "")), (
            f"the message must name the id the caller sent; got {resp.json()!r}"
        )

    def test_a_detached_parent_on_no_block_is_placed_by_the_block_above(self, anchored):
        """A line in no block still has a group, and the resolver has to use it.

        ``test_a_detached_parent_shown_on_another_block_is_rejected`` lands the
        parent's line *inside* a replacement block, as every other detached test
        here does.  The branch below that one -- the parent's line falling
        between blocks, grouped under the last block start at or above it -- is
        the placement #497 made first-class, and it is what the view shows for a
        comment whose paragraph was deleted outright rather than rewritten.

        Answering "no placement" there would read as "no opinion" and accept
        this reply, which then renders at ``depth 0`` in a group the reader will
        not find the parent in.  So the fallback is not a tidy-up: it decides a
        create.
        """
        import server

        root = _post_json_comment(
            anchored["client"], line_start=3, body="on the first paragraph"
        )
        # Delete the paragraph the root sits on without replacing it: L3 is now
        # one of the blank lines between the heading and the tail.
        anchored["doc"].write_text("# Title\n\n\n\n\nlast paragraph\n")

        payload = server.build_view_payload("doc.md")
        assert _find(payload, root["id"])["detached"] is True, (
            "fixture assumption: the root's text is gone, so its stored block_id "
            "names no block and the line fallback is what places it"
        )
        assert _group_of(payload, root["id"]) == 1, (
            "fixture assumption: L3 is in no block now, so the view groups the "
            "root under the heading above it -- not the L6 block the reply is on"
        )

        resp = self._post(anchored["client"], root["id"], line_start=6)
        assert resp.status_code == 422, (
            "the reader is shown the parent under the L1 heading while this reply "
            "is written on the L6 block, so it would come back a root in another "
            "group -- the guard must place a between-blocks parent the way the "
            f"view does; got {resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )
        assert str(root["id"]) in str(resp.json().get("detail", "")), (
            f"the message must name the id the caller sent; got {resp.json()!r}"
        )

    def test_a_between_blocks_parent_takes_the_last_block_above_not_the_first(
        self, anchored
    ):
        """"The block above" has to mean the nearest one, and nothing pinned that.

        ``test_a_detached_parent_on_no_block_is_placed_by_the_block_above``
        above pins that the fallback exists and looks *upward*, but not how far
        up it stops: its fixture leaves only two candidate blocks and the right
        answer happens to be the first of them, so "last block at or above" and
        "first block in the file" are the same number there.  They are the same
        number in every other test too -- returning ``blocks[0]["start_line"]``
        unconditionally from ``_nearest_block_start()`` leaves the whole suite
        green, and that mutant is not equivalent.

        It decides a create.  Here the parent's line falls below the third
        block, and the reply is written on the first: grouped where the view
        actually puts it the two are on different blocks and the reply is the
        201-that-is-not-a-reply this guard refuses, while grouped under the
        first block they collide and it is accepted.  The helper is shared with
        the read path (``_resolve_comment_blocks()``), so the same distance
        question decides where the reader is shown the parent -- which is why
        this asserts the group and the status, not just the status.
        """
        import server

        root = _post_json_comment(
            anchored["client"], line_start=7, body="on the last paragraph"
        )
        # Delete the paragraph the root sits on without replacing it, keeping
        # the line: L7 is now blank, with three blocks (L1, L3, L5) above it.
        anchored["doc"].write_text("# Title\n\nfirst paragraph\n\ntarget paragraph\n\n\n")

        payload = server.build_view_payload("doc.md")
        assert _find(payload, root["id"])["detached"] is True, (
            "fixture assumption: the root's text is gone, so its stored block_id "
            "names no block and the line fallback is what places it"
        )
        assert _group_of(payload, root["id"]) == 5, (
            "the reader is shown a between-blocks comment under the block "
            "immediately above it (L5), not under the first block in the file "
            "(L1) -- a comment about the end of a document must not surface at "
            "the top of it"
        )

        resp = self._post(anchored["client"], root["id"], line_start=1)
        assert resp.status_code == 422, (
            "the parent is displayed under the L5 block and this reply is "
            "written on the L1 heading, so it would come back a root in another "
            "group; accepting it means the guard measured the distance to the "
            f"parent as zero when the view did not; got {resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )
        assert str(root["id"]) in str(resp.json().get("detail", "")), (
            f"the message must name the id the caller sent; got {resp.json()!r}"
        )

    def test_a_parent_above_every_block_takes_the_first_block_not_the_last(
        self, anchored
    ):
        """The "(first block if none)" clause decides a create too, and nothing pinned it.

        ``test_a_between_blocks_parent_takes_the_last_block_above_not_the_first``
        pins the *loop* in ``_nearest_block_start()`` -- how far up it stops.
        This pins its *initializer*, the answer for a line with no block above
        it at all.  Mutating that to ``blocks[-1]["start_line"]`` leaves the
        whole suite green, and it is not equivalent: it is the bug the test
        above refuses, reached from the other end of the document.

        No other fixture in the suite can tell the two apart, because every
        other one starts a block on L1 -- most open with a heading, the rest
        with body text or marp front matter.  With the first block on the first
        line no comment can sit above it, so the loop overwrites the
        initializer on its first pass and the initial value never reaches a
        caller.  The document below is what separates them: it opens with
        blank lines, so its first block starts at L3 and a comment on L1 is
        answered by the initializer alone.

        It decides a create for the same reason the loop does: the parent's
        placement is what the reader is shown, and a reply written on a
        different block comes back a root at ``depth 0`` in a group the reader
        will not find the parent in.  Here production groups the parent under
        the first block and refuses the reply written on the last one, while
        the mutant groups it under the last block -- a comment about the top of
        a document surfacing at the bottom of it -- and accepts.
        """
        import server

        root = _post_json_comment(
            anchored["client"], line_start=1, body="on the heading"
        )
        # Retitle the heading (so the stored block_id matches nothing) and push
        # the document down: L1 is now blank, with every block *below* it.
        anchored["doc"].write_text(
            "\n\n# Retitled\n\nfirst paragraph\n\nlast paragraph\n"
        )

        payload = server.build_view_payload("doc.md")
        assert _find(payload, root["id"])["detached"] is True, (
            "fixture assumption: the heading was retitled, so the root's stored "
            "block_id names no block and the line fallback is what places it"
        )
        assert _group_of(payload, root["id"]) == 3, (
            "the reader is shown a comment that sits above every block under the "
            "*first* block (L3), not the last one (L7) -- a comment about the top "
            "of a document must not surface at the bottom of it"
        )

        resp = self._post(anchored["client"], root["id"], line_start=7)
        assert resp.status_code == 422, (
            "the parent is displayed under the L3 block and this reply is written "
            "on the L7 one, so it would come back a root in another group; "
            "accepting it means the guard placed a parent above every block at "
            f"the end of the file when the view placed it at the start; got "
            f"{resp.status_code} "
            f"{resp.json() if resp.status_code == 201 else resp.text}"
        )
        assert str(root["id"]) in str(resp.json().get("detail", "")), (
            f"the message must name the id the caller sent; got {resp.json()!r}"
        )

    def test_the_parent_is_resolved_by_digest_not_by_stored_id(self, anchored):
        """Renumbering is not detachment, and the guard must not read it as one.

        ``block_id`` is ``digest-occurrence`` and #467 made the digest decide:
        deleting an earlier byte-identical copy moves a block's ordinal while
        its text — the thing the comment was written about — is untouched, so
        the view still places the comment on it.  A guard that looked the
        stored id up directly would miss, fall through to the line, and answer
        about a block the reader does not see the parent on.

        Here the two answers differ on purpose: after the deletion the parent's
        text is at L3 while its stored *line* (L5) has come to hold a different
        paragraph.  The digest answer is the reply's own block, so this create
        is legal; the stored-id answer is the L5 block, which would refuse it.
        """
        import server

        bee = "bee paragraph, the one being replied to"
        cee = "cee paragraph, a different block entirely"
        dee = "dee paragraph, further down"
        anchored["doc"].write_text(f"{bee}\n\n{cee}\n\n{bee}\n\n{dee}\n")

        root = _post_json_comment(
            anchored["client"], line_start=5, body="on the second copy"
        )
        assert root["block_id"].endswith("-2"), (
            "fixture assumption: the comment is on the *second* copy, so the "
            f"deletion below moves its ordinal; got {root['block_id']}"
        )

        # Delete the first copy.  The commented text is still in the document —
        # now at L3, as occurrence 1 — and L5 now holds the "dee" paragraph.
        anchored["doc"].write_text(f"{cee}\n\n{bee}\n\n{dee}\n")

        payload = server.build_view_payload("doc.md")
        assert _group_of(payload, root["id"]) == 3, (
            "fixture assumption: the view follows the text to L3, so a guard that "
            "agrees with the view must too"
        )

        resp = self._post(anchored["client"], root["id"], line_start=3)
        assert resp.status_code == 201, (
            "the parent's own text is at L3 and this reply is written on it, so "
            "they are the same block however the occurrence number was renumbered; "
            f"got {resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == root["id"], (
            "the explicit parent must survive the guard"
        )
        payload = server.build_view_payload("doc.md")
        assert _find(payload, resp.json()["id"])["depth"] == 1, (
            "and the create the guard let through is a properly nested reply, "
            "which is what makes refusing it wrong"
        )

    def test_the_parent_is_resolved_against_this_file_only(self, anchored):
        """A block matching in a *sibling* file says nothing about this one.

        The resolver renders the file being commented on, not the directory.
        Identical text in a sibling has an identical ``block_id`` — that is what
        ``test_a_parent_on_the_same_text_in_another_file_is_rejected`` turns on
        — so a resolver that searched more widely would find the parent's old
        text next door, answer with a block of another document, and refuse a
        reply the reader can see is properly threaded here.
        """
        import server

        root = _post_json_comment(anchored["client"], line_start=5, body="a real root")
        # The parent's old text now lives only in the sibling; in this file the
        # paragraph has been rewritten, so the parent is detached *here*.
        (anchored["dir"] / "sibling.md").write_text(DOC_465)
        anchored["doc"].write_text(
            "# Title\n\nfirst paragraph\n\ntarget paragraph, revised\n\nlast paragraph\n"
        )

        resp = self._post(anchored["client"], root["id"])
        assert resp.status_code == 201, (
            "the parent is displayed on this file's L5 block, which is the block "
            "the reply is written on; the copy of its old text in sibling.md is "
            f"another document and not an answer about this one; got "
            f"{resp.status_code} {resp.text}"
        )
        payload = server.build_view_payload("doc.md")
        assert _find(payload, resp.json()["id"])["depth"] == 1, (
            "and the reply renders nested under it, so refusing it would have been "
            "wrong about a thread the reader can see"
        )

    def test_a_reply_written_on_no_block_may_still_name_a_parent(self, anchored):
        """The same rule seen from the other side: L2 is the blank line between blocks.

        ``test_a_line_in_no_block_does_not_thread`` pins that such a comment
        derives no parent.  It may still be *given* one: there is no block
        identity on this side either, so nothing here is provably cross-block.
        """
        root = _post_json_comment(anchored["client"], line_start=5, body="a real root")

        resp = self._post(anchored["client"], root["id"], line_start=2)
        assert resp.status_code == 201, (
            "a comment on no block has no block identity to be in conflict with, "
            f"so an explicit parent is still legal; got {resp.status_code} {resp.text}"
        )
        assert resp.json()["parent_id"] == root["id"], (
            "the explicit parent must survive -- derivation answers None for a "
            "comment on no block, so this asserts the guard let it through"
        )


class TestSqliteIntegerBound:
    """The shared bound itself, pinned from both sides (#477 item 2).

    ``_fits_sqlite_integer`` decides two unrelated call sites -- ``parent_id``
    on create (#473) and ``comment_id`` on the resolve routes (#475) -- so the
    bound is worth an assertion that does not run through either of them.

    It is here rather than only at the call sites because the *legal* side of
    the bound is hard to reach through the ``parent_id`` API -- but only at one
    end, and this docstring used to say "unobservable" flatly.  That
    overstatement is what let #478 and then #479 generalise a one-ended argument
    across the whole range; the two ends have to be argued separately.

    At the **top** it really is unreachable.  Every id that is in range but names
    no comment answers 422, exactly as an out-of-range one does, so ``2**63-1``
    cannot distinguish a correct bound from one narrowed by one.  The obvious
    workaround -- put a real comment at ``2**63-1`` and reply to it -- does not
    work either: ``comments.id`` is ``AUTOINCREMENT``, so a row at the maximum
    makes the *next* insert fail with ``sqlite3.OperationalError: database or
    disk is full``, and the reply can never be created.

    At the **floor** it is reachable, because ``AUTOINCREMENT`` constrains only
    the maximum.  A row at ``-(2**63)`` inserts fine and leaves later inserts
    getting their ordinary ids, so it can be replied to and the accepting side
    shows up at the call site as 201-vs-422.  That is pinned by
    ``TestOutOfRangeExplicitParent::test_a_parent_at_the_floor_is_accepted_when_it_names_a_real_row``.

    (The resolve routes do not have this problem: an in-range unknown id is
    answered **404** there against the out-of-range **422**, which is
    distinguishable, and is pinned by ``TestOutOfRangeCommentIdOnResolve``.
    Before #475 item 2 the distinguishing answer was a 303; the codes changed,
    the argument did not.  What the argument needs is only that the two differ,
    which is why item 2 did not fold unknown into 422.)

    So: the rejecting side is pinned at both call sites; the accepting side is
    pinned here at both ends, and at the ``parent_id`` call site at the floor.
    """

    def test_the_largest_legal_integer_fits(self):
        from server import _fits_sqlite_integer

        assert _fits_sqlite_integer(2**63 - 1) is True, (
            "2**63-1 is the largest value SQLite will bind, so narrowing the "
            "bound by one would start rejecting a perfectly legal id"
        )

    def test_one_past_the_top_does_not_fit(self):
        from server import _fits_sqlite_integer

        assert _fits_sqlite_integer(2**63) is False, (
            "2**63 is the first value the driver refuses to bind, so it must "
            "not be let through to raise OverflowError inside a query"
        )

    def test_the_floor_is_inclusive(self):
        from server import _fits_sqlite_integer

        assert _fits_sqlite_integer(-(2**63)) is True, (
            "-(2**63) binds fine, so the lower bound is inclusive"
        )

    def test_one_below_the_floor_does_not_fit(self):
        from server import _fits_sqlite_integer

        assert _fits_sqlite_integer(-(2**63) - 1) is False, (
            "one below the floor overflows just as readily as one above the top"
        )


class TestThreadRendering:
    """``comments_by_block`` expresses the thread so the client can nest it."""

    def _group(self, payload, start_line=5):
        return payload["comments_by_block"][start_line]

    def test_a_chain_renders_root_first_then_its_replies(self, anchored):
        from server import build_view_payload

        c1 = _post_json_comment(anchored["client"], line_start=5, body="root")
        c2 = _post_json_comment(anchored["client"], line_start=5, body="reply")
        c3 = _post_json_comment(anchored["client"], line_start=5, body="reply of reply")

        group = self._group(build_view_payload("doc.md"))
        assert [c["id"] for c in group] == [c1["id"], c2["id"], c3["id"]]
        assert [c["body"] for c in group] == ["root", "reply", "reply of reply"]
        # Nested under the *root*, not one indent deeper per hop: the chain is
        # a threading device, the display is a two-level thread.
        assert [c["depth"] for c in group] == [0, 1, 1]

    def test_a_late_reply_sorts_under_its_root_not_by_time(self, anchored):
        """The discriminator against the old flat, time-ordered list."""
        from server import build_view_payload

        c1 = _post_json_comment(anchored["client"], line_start=5, body="root one")
        root_two = _insert_row(anchored, body="root two", block_id=c1["block_id"])
        late = _insert_row(
            anchored, body="late reply", block_id=c1["block_id"], parent_id=c1["id"]
        )

        group = self._group(build_view_payload("doc.md"))
        assert [c["body"] for c in group] == ["root one", "late reply", "root two"], (
            "created_at order would be root one, root two, late reply"
        )
        assert [c["id"] for c in group] == [c1["id"], late["id"], root_two["id"]]
        assert [c["depth"] for c in group] == [0, 1, 0]

    def test_a_reply_whose_parent_is_not_on_this_file_still_renders(self, anchored):
        """Never lose a comment: an unreachable parent means root, not gone."""
        from server import build_view_payload

        (anchored["dir"] / "other.md").write_text(DOC_465)
        c1 = _post_json_comment(anchored["client"], line_start=5, body="on doc")
        elsewhere = _insert_row(
            anchored, body="on another file", block_id=c1["block_id"], path="other.md"
        )
        orphan = _insert_row(
            anchored,
            body="reply to a comment on another file",
            block_id=c1["block_id"],
            parent_id=elsewhere["id"],
        )

        group = self._group(build_view_payload("doc.md"))
        bodies = [c["body"] for c in group]
        assert "reply to a comment on another file" in bodies
        assert _find(build_view_payload("doc.md"), orphan["id"])["depth"] == 0

    def test_legacy_rows_with_no_block_id_still_render(self, anchored):
        from server import build_view_payload

        legacy = _insert_row(anchored, body="pre-#465 row", block_id=None)
        assert _find(build_view_payload("doc.md"), legacy["id"]) is not None

    def test_re_rendering_neither_re_threads_nor_duplicates(self, anchored):
        from server import build_view_payload

        _post_json_comment(anchored["client"], line_start=5, body="root")
        _post_json_comment(anchored["client"], line_start=5, body="reply")
        _post_json_comment(anchored["client"], line_start=3, body="elsewhere")

        def shape():
            payload = build_view_payload("doc.md")
            return {
                start: [(c["id"], c["parent_id"], c["depth"]) for c in group]
                for start, group in payload["comments_by_block"].items()
            }

        def stored_parents():
            from db import get_connection

            conn = get_connection(anchored["db"])
            try:
                return {
                    r["id"]: r["parent_id"]
                    for r in conn.execute(
                        "SELECT id, parent_id FROM comments"
                    ).fetchall()
                }
            finally:
                conn.close()

        first, parents_before = shape(), stored_parents()
        second = shape()
        assert second == first
        assert stored_parents() == parents_before

    def test_app_js_nests_replies_by_depth(self, anchored):
        """The card class must follow the resolved thread, not raw parent_id.

        A reply whose root is not on this file renders at depth 0; keying the
        indent on ``parent_id`` would indent it under nothing.
        """
        js = anchored["client"].get("/static/app.js").text
        assert "c.depth" in js, "app.js must read the depth the server resolved"

    def test_css_indents_a_nested_reply(self, anchored):
        css = anchored["client"].get("/static/style.css").text
        rule = re.search(r"\.comment-card\.reply\s*\{([^}]*)\}", css)
        assert rule, "style.css must keep a .comment-card.reply rule"
        assert "margin-left" in rule.group(1), (
            "a reply must be indented under its root, not only accented"
        )


class TestThreadRenderingAcrossBlocks:
    """A thread is read per block, so it must be resolved per block (#469 review).

    Resolving roots file-wide while rendering block-wide indents a reply under
    a root the reader cannot see, and sorts it by that absent root's clock —
    which can put it above the root of the block it is actually in.
    """

    def test_a_reply_grouped_apart_from_its_root_renders_as_a_root(self, anchored):
        from server import build_view_payload

        first = _post_json_comment(anchored["client"], line_start=3, body="root on L3")
        last = _post_json_comment(anchored["client"], line_start=7, body="root on L7")
        # A reply to the L3 root that is grouped under the L7 block: the shape a
        # detached root leaves behind, and the shape of the CLI-set parents in
        # the live DB.
        stray = _insert_row(
            anchored,
            body="reply to the L3 root, grouped on L7",
            block_id=last["block_id"],
            parent_id=first["id"],
            line=7,
        )

        group = build_view_payload("doc.md")["comments_by_block"][7]
        assert [c["body"] for c in group] == [
            "root on L7",
            "reply to the L3 root, grouped on L7",
        ], "the block's own root must lead its list"
        assert [c["id"] for c in group] == [last["id"], stray["id"]]
        assert [c["depth"] for c in group] == [0, 0], (
            "a reply whose root is in another block has nothing here to nest under"
        )

    def test_a_three_deep_chain_outranks_a_root_written_between_its_hops(
        self, anchored
    ):
        """The root walk has to be multi-hop, not one hop.

        A single hop would file the third comment under the *second*, whose
        clock is later than the competing root's — so the chain would be split
        around it.
        """
        from server import build_view_payload

        c1 = _post_json_comment(anchored["client"], line_start=5, body="root")
        rival = _insert_row(anchored, body="rival root", block_id=c1["block_id"])
        c2 = _insert_row(
            anchored, body="reply", block_id=c1["block_id"], parent_id=c1["id"]
        )
        c3 = _insert_row(
            anchored, body="reply of reply", block_id=c1["block_id"], parent_id=c2["id"]
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["body"] for c in group] == [
            "root",
            "reply",
            "reply of reply",
            "rival root",
        ]
        assert [c["id"] for c in group] == [
            c1["id"], c2["id"], c3["id"], rival["id"]
        ]
        assert [c["depth"] for c in group] == [0, 1, 1, 0]

    def test_a_parent_cycle_does_not_hang_the_view(self, anchored):
        """The visited set is load-bearing: without it the view never answers.

        Run on a thread so a regression fails the suite instead of wedging it.
        """
        import threading

        from db import get_connection
        from server import build_view_payload

        a = _post_json_comment(anchored["client"], line_start=5, body="a")
        b = _post_json_comment(anchored["client"], line_start=5, body="b")
        conn = get_connection(anchored["db"])
        try:
            conn.execute(
                "UPDATE comments SET parent_id = ? WHERE id = ?", (b["id"], a["id"])
            )
            conn.commit()
        finally:
            conn.close()

        done = {}
        worker = threading.Thread(
            target=lambda: done.setdefault("payload", build_view_payload("doc.md")),
            daemon=True,
        )
        worker.start()
        worker.join(timeout=15)
        assert not worker.is_alive(), (
            "build_view_payload() never returned: a parent cycle spun forever"
        )
        rendered = {c["id"] for c in _payload_comments(done["payload"])}
        assert rendered == {a["id"], b["id"]}, "a cycle must not lose a comment"

    def test_comments_written_in_the_same_microsecond_still_chain(
        self, anchored, monkeypatch
    ):
        """Identical timestamps must not collapse "the latest" into a tie."""
        import db

        monkeypatch.setattr(db, "_now_iso", lambda: "2026-08-16T14:00:00+00:00")
        c1 = _post_json_comment(anchored["client"], line_start=5, body="one")
        c2 = _post_json_comment(anchored["client"], line_start=5, body="two")
        c3 = _post_json_comment(anchored["client"], line_start=5, body="three")

        assert c1["created_at"] == c3["created_at"], "fixture assumption: a real tie"
        assert (c2["parent_id"], c3["parent_id"]) == (c1["id"], c2["id"])

    def test_two_same_instant_roots_keep_their_own_replies_together(
        self, anchored, monkeypatch
    ):
        """The root **id** tiebreak in the sort key, which nothing else defends.

        ``build_view_payload`` sorts by the *root's* ``created_at`` before the
        comment's own, so a thread travels as a unit.  When two roots on one
        block share an instant that term ties, and only the root's **id** keeps
        the two threads apart: without it the replies fall back to their own
        clocks and interleave across the roots.

        The suite already covers ties (``..._same_microsecond_still_chain``, a
        different tiebreak -- ``latest_comment_in_block``'s ``id DESC``) and
        already covers a reply outranking a later root
        (``..._late_reply_sorts_under_its_root_not_by_time``, which needs no
        tie).  Neither builds the shape that discriminates: two roots that tie
        on ``created_at`` where a reply has to sort between them.  Deleting
        ``root_of[c["id"]]["id"]`` from the sort key left all 442 tests green
        before this one (#471).

        The **tie is the load-bearing part**, and only the tie -- measured, not
        assumed.  Probed against the mutant: the same four comments with the
        roots one second apart pass it (the ``created_at`` term above already
        separates the threads, so the id term is never reached), while a
        cut-down variant with a single reply still fails it.  The second reply
        is kept anyway because it renders the actual symptom -- two threads
        interleaved rather than one comment misplaced -- and it is the shape
        #471 asked for.
        """
        import db
        from server import build_view_payload

        clock = {"now": "2026-08-16T14:00:00+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root_a = _post_json_comment(anchored["client"], line_start=5, body="root A")
        # A second root on a block that already has one: the routes will not
        # produce this shape (they would reply to root A), so it is written
        # straight to the DB -- at the *same* instant, which is the tie.
        root_b = _insert_row(anchored, body="root B", block_id=root_a["block_id"])
        clock["now"] = "2026-08-16T14:00:01+00:00"
        reply_b = _insert_row(
            anchored,
            body="reply to B",
            block_id=root_a["block_id"],
            parent_id=root_b["id"],
        )
        clock["now"] = "2026-08-16T14:00:02+00:00"
        reply_a = _insert_row(
            anchored,
            body="reply to A",
            block_id=root_a["block_id"],
            parent_id=root_a["id"],
        )

        assert root_b["parent_id"] is None, (
            "fixture: B must be a second *root*; a reply to A would leave one "
            "thread here and nothing for the root-id term to order"
        )
        assert (reply_a["parent_id"], reply_b["parent_id"]) == (
            root_a["id"],
            root_b["id"],
        ), (
            "fixture: each reply must hang off its own root -- a helper that "
            "quietly built a different linkage would leave the order assert "
            "below defending nothing"
        )
        assert root_a["created_at"] == root_b["created_at"], (
            "fixture: the roots must genuinely tie on created_at, or the sort "
            "never reaches the root-id term this test is about"
        )
        assert root_a["id"] < root_b["id"], (
            "fixture: A is the lower-*id* root -- the roots tie on created_at "
            "(asserted above), so the id is the only thing that can put A's "
            "thread first"
        )
        assert reply_b["created_at"] < reply_a["created_at"], (
            "fixture: the replies' own clocks run *opposite* to their roots' "
            "ids, so plain time order would give a visibly different answer "
            "from thread order (this is what makes the failure legible; the "
            "roots' tie above is what makes the defect observable at all)"
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["body"] for c in group] == [
            "root A",
            "reply to A",
            "root B",
            "reply to B",
        ], (
            "each thread must render as a unit; dropping the root-id tiebreak "
            "gives root A, root B, reply to B, reply to A -- the replies "
            "ordered by their own clocks, torn away from their roots"
        )
        assert [c["id"] for c in group] == [
            root_a["id"],
            reply_a["id"],
            root_b["id"],
            reply_b["id"],
        ]
        assert [c["depth"] for c in group] == [0, 1, 0, 1], (
            "depth is *not* the discriminator here -- the mutant preserves it, "
            "which is why the assertion above is on the order"
        )

    def test_threads_order_by_their_roots_clock_not_by_their_roots_id(
        self, anchored, monkeypatch
    ):
        """The root **created_at** term in the sort key, the one above the id.

        #482 asked whether ``root_of[c["id"]]["created_at"]`` can be observed at
        all, or whether it is an equivalent mutant: ``create_comment`` stamps
        ``created_at`` from ``_now_iso()`` at insert, so if ids and timestamps
        always rose together, sorting threads by root id alone would give the
        same answer and deleting the term would be undetectable.  It is not
        equivalent.  ``_now_iso()`` reads ``datetime.now(timezone.utc)`` -- the
        **wall** clock, which is not monotonic -- while ``comments.id`` is
        ``AUTOINCREMENT`` and only ever rises.  An NTP step, a ``date -s``, or a
        restored VM snapshot between two inserts is enough for a *later* row to
        carry an *earlier* stamp, and that DB comes straight out of
        ``create_comment``: no re-keying, no restored backup, no imported
        thread.  This test builds it by stepping the patched clock backwards.

        So the two orders disagree, and the term decides which one wins: threads
        render oldest-root-first by the clock, and the id below it only breaks
        genuine ties (#471).  Deleting this term left all 443 tests green after
        #481 -- it was undefended, not unobservable.

        What is load-bearing here was measured, not assumed.  Under the deletion
        the *disagreement* is the whole discriminator: two bare roots whose
        clock order opposes their id order already kill it, and the same shape
        with the clocks running forwards does not.  The replies are kept because
        they kill a **different** mutation -- reading the comment's own
        ``created_at`` instead of its root's, which is what makes a thread
        travel as a unit -- and a two-root fixture is blind to that one, since
        a root *is* its own root.  Together they pin both halves of the term.
        """
        import db
        from server import build_view_payload

        clock = {"now": "2026-08-16T14:00:05+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root_a = _post_json_comment(anchored["client"], line_start=5, body="root A")
        # The wall clock steps *backwards* before the next insert, so root B
        # gets the higher id and the earlier stamp.  A second root on a block
        # that already has one is written straight to the DB, as the routes
        # would reply to root A instead (#465).
        clock["now"] = "2026-08-16T14:00:00+00:00"
        root_b = _insert_row(anchored, body="root B", block_id=root_a["block_id"])
        clock["now"] = "2026-08-16T14:00:20+00:00"
        reply_a = _insert_row(
            anchored,
            body="reply to A",
            block_id=root_a["block_id"],
            parent_id=root_a["id"],
        )
        clock["now"] = "2026-08-16T14:00:21+00:00"
        reply_b = _insert_row(
            anchored,
            body="reply to B",
            block_id=root_a["block_id"],
            parent_id=root_b["id"],
        )

        assert root_a["id"] < root_b["id"], (
            "fixture: B is the *later* insert, so AUTOINCREMENT must give it "
            "the higher id"
        )
        assert root_b["created_at"] < root_a["created_at"], (
            "fixture: the roots' clock order must genuinely oppose their id "
            "order, or the two candidate sort keys agree and the deleted term "
            "is unobservable -- this opposition is the whole discriminator"
        )
        assert root_b["parent_id"] is None, (
            "fixture: B must be a second *root*, not a reply to A"
        )
        assert (reply_a["parent_id"], reply_b["parent_id"]) == (
            root_a["id"],
            root_b["id"],
        ), (
            "fixture: each reply must hang off its own root, or the replies "
            "stop testing that a thread is ordered by its root's clock"
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["body"] for c in group] == [
            "root B",
            "reply to B",
            "root A",
            "reply to A",
        ], (
            "threads render by their root's clock, oldest first; dropping "
            "root_of[c][\"created_at\"] falls back to the root id and gives "
            "root A, reply to A, root B, reply to B -- insertion order, not "
            "time order"
        )
        assert [c["id"] for c in group] == [
            root_b["id"],
            reply_b["id"],
            root_a["id"],
            reply_a["id"],
        ]
        assert [c["depth"] for c in group] == [0, 1, 0, 1], (
            "depth is not the discriminator -- neither mutation changes any "
            "comment's own depth; this sequence moves only because the order "
            "does (the substitution gives [0, 0, 1, 1])"
        )

    def test_resolving_a_root_does_not_move_its_thread(self, anchored, monkeypatch):
        """The root sort term is a **created_at**, not just any timestamp.

        #483 pinned two axes of ``root_of[c["id"]]["created_at"]``: that it is a
        timestamp at all (delete it and the order falls back to the root id),
        and that it is the *root's* stamp rather than the comment's own.  A
        third axis was left open, and #484 measured it surviving: swapping the
        field to ``root_of[c["id"]]["updated_at"]`` left all 444 tests green.

        It is not an equivalent mutant.  ``create_comment`` writes both stamps
        from the same ``now``, which is why every test that only ever *creates*
        rows is blind to the swap -- but ``resolve_comment`` and
        ``unresolve_comment`` both ``SET updated_at = ?`` without touching
        ``created_at``.  So under the mutation, resolving a comment reorders the
        block it sits in: the resolved root carries the newest stamp and its
        whole thread drops to the bottom.

        That is a user-visible contract nothing stated before this test:
        **resolving a comment is a state change, not a reordering.**  A reader
        who ticks off the oldest thread should not watch the block rearrange
        underneath them.

        The fixture is deliberately the mirror image of the test above.  There
        the clocks *oppose* the ids, because a disagreement is what makes the
        deletion observable; here they run **with** the ids, so neither the
        deletion nor the root-vs-own substitution can be what turns this red --
        the ``updated_at`` divergence introduced by the resolve is the only
        discriminator left.  Measured: drop the resolve and the swap mutant
        survives this test; keep it and the mutant dies on the order assert.
        """
        import db
        from server import build_view_payload

        clock = {"now": "2026-08-16T14:00:00+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root_a = _post_json_comment(anchored["client"], line_start=5, body="root A")
        # A second root on a block that already has one is written straight to
        # the DB: the create routes would make it a reply to root A (#465).
        clock["now"] = "2026-08-16T14:00:05+00:00"
        root_b = _insert_row(anchored, body="root B", block_id=root_a["block_id"])

        assert root_a["id"] < root_b["id"], (
            "fixture: B is the later insert, so AUTOINCREMENT must give it the "
            "higher id"
        )
        assert root_a["created_at"] < root_b["created_at"], (
            "fixture: the clocks must run *with* the ids here, so that neither "
            "deleting the term nor reading the comment's own created_at can be "
            "what reddens this test -- only the updated_at swap can"
        )
        assert root_b["parent_id"] is None, (
            "fixture: B must be a second *root*, not a reply to A"
        )

        # Resolve the *older* root, last, so its updated_at is the newest in
        # the block while its created_at stays the oldest.
        clock["now"] = "2026-08-16T14:00:20+00:00"
        resp = anchored["client"].post(
            f"/comment/{root_a['id']}/resolve",
            data={"path": "doc.md"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text

        assert _is_resolved(anchored["db"], root_a["id"]) is True, (
            "fixture: the resolve must have landed on A"
        )
        assert _is_resolved(anchored["db"], root_b["id"]) is False, (
            "fixture: ... and only on A, or both stamps would move together"
        )

        stamps = _stamps_of(anchored["db"], [root_a["id"], root_b["id"]])
        bumped, untouched = stamps[root_a["id"]], stamps[root_b["id"]]
        assert bumped["created_at"] == root_a["created_at"], (
            "fixture: resolving must not touch created_at, or the two fields "
            "cannot be told apart by this test"
        )
        assert bumped["updated_at"] > untouched["updated_at"], (
            "fixture: the resolve must make the *older* root carry the *newer* "
            "updated_at -- that inversion is the whole discriminator"
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["body"] for c in group] == ["root A", "root B"], (
            "resolving a root must not move its thread; sorting on "
            'root_of[c]["updated_at"] instead of ["created_at"] sends the '
            "just-resolved root to the bottom of its block"
        )
        assert [c["id"] for c in group] == [root_a["id"], root_b["id"]]
        assert [c["resolved"] for c in group] == [1, 0], (
            "the payload must carry the resolved state through to the reader: "
            "static/app.js reads c.resolved for the card class, the 'resolved' "
            "label and the Resolve/Unresolve button, and nothing else pins it"
        )

    def test_resolving_a_reply_does_not_move_it_among_its_siblings(
        self, anchored, monkeypatch
    ):
        """The within-thread tiebreak is a **created_at** too, not any timestamp.

        The test above pins the *root* term of ``build_view_payload``'s sort key
        against ``created_at`` -> ``updated_at``.  #486 measured the identical
        swap surviving on the *next* timestamp in the same key -- the bare
        ``c["created_at"]`` three terms further down (L655 to that one's L652,
        with the root id and ``c["depth"]`` in between), which orders comments
        *inside* a thread: 445 passed at ``ecb6252``.

        Not an equivalent mutant, and for the same reason as one level up:
        ``create_comment`` writes both stamps from a single ``now``, so a test
        that only creates rows cannot tell the fields apart, while
        ``resolve_comment`` and ``unresolve_comment`` ``SET updated_at = ?`` and
        leave ``created_at`` alone.  Under the mutation, resolving a reply gives
        it the newest stamp in its thread and it jumps past its own siblings --
        the reply-level form of the block-level defect the test above fixed, and
        the same contract: **resolving a comment is a state change, not a
        reordering.**

        The fixture is entirely route-built (no ``_insert_row``): three comments
        on one block are threaded root -> reply -> reply by #465's rule, so both
        replies share a root and render at depth 1.  That is what makes this test
        deaf to the neighbouring terms -- ``root_of[c["id"]]["created_at"]`` and
        ``root_of[c["id"]]["id"]`` are *constant* across a single thread, so
        deleting or substituting either cannot reorder this group.  Measured over
        five mutations of the other terms -- each root term deleted, the root
        ``created_at`` swapped to ``updated_at``, ``c["depth"]`` deleted, and
        this term replaced by the root's ``created_at`` -- and this test stays
        green under every one of them.  Only the ``c["created_at"]`` ->
        ``c["updated_at"]`` swap turns it red.

        Ablated under that swap: with no resolve the mutant survives, and
        resolving the *newer* reply leaves it alive too (its stamp was already
        the newest).  Only resolving the *older* reply kills it -- so the resolve
        and the choice of which reply are both load-bearing, and the third
        comment is not decorative: two siblings are the minimum that can be
        reordered relative to each other.
        """
        import db
        from server import build_view_payload

        clock = {"now": "2026-08-16T14:00:00+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root = _post_json_comment(anchored["client"], line_start=5, body="root")
        clock["now"] = "2026-08-16T14:00:05+00:00"
        older = _post_json_comment(anchored["client"], line_start=5, body="reply one")
        clock["now"] = "2026-08-16T14:00:10+00:00"
        newer = _post_json_comment(anchored["client"], line_start=5, body="reply two")

        assert (older["parent_id"], newer["parent_id"]) == (root["id"], older["id"]), (
            "fixture: #465's rule must have chained all three into one thread, "
            "so that the root terms of the sort key are constant across them"
        )
        assert root["id"] < older["id"] < newer["id"], (
            "fixture: AUTOINCREMENT must give the later inserts higher ids"
        )
        assert root["created_at"] < older["created_at"] < newer["created_at"], (
            "fixture: the clocks must run *with* the ids, so that neither "
            "deleting the tiebreak nor falling back to c[\"id\"] can be what "
            "reddens this test -- only the updated_at swap can"
        )

        # Resolve the *older* reply, last, so its updated_at is the newest in
        # the thread while its created_at stays the older of the two.
        clock["now"] = "2026-08-16T14:00:20+00:00"
        resp = anchored["client"].post(
            f"/comment/{older['id']}/resolve",
            data={"path": "doc.md"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text

        assert _is_resolved(anchored["db"], older["id"]) is True, (
            "fixture: the resolve must have landed on the older reply"
        )
        assert _is_resolved(anchored["db"], newer["id"]) is False, (
            "fixture: ... and only on it, or the stamps would move together"
        )

        stamps = _stamps_of(anchored["db"], [older["id"], newer["id"]])
        bumped, untouched = stamps[older["id"]], stamps[newer["id"]]
        assert bumped["created_at"] == older["created_at"], (
            "fixture: resolving must not touch created_at, or the two fields "
            "cannot be told apart by this test"
        )
        assert bumped["updated_at"] > untouched["updated_at"], (
            "fixture: the resolve must make the *older* reply carry the *newer* "
            "updated_at -- that inversion is the whole discriminator"
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["body"] for c in group] == ["root", "reply one", "reply two"], (
            "resolving a reply must not move it among its siblings; sorting the "
            'within-thread tiebreak on c["updated_at"] instead of '
            'c["created_at"] sends the just-resolved reply to the bottom of its '
            "own thread"
        )
        assert [c["id"] for c in group] == [root["id"], older["id"], newer["id"]]
        assert [c["depth"] for c in group] == [0, 1, 1], (
            "both replies must nest under the one root -- that is why the root "
            "terms above the tiebreak cannot be what orders this group"
        )
        assert [c["resolved"] for c in group] == [0, 1, 0], (
            "the payload must carry the resolved state through to the reader, "
            "and carry it on the comment it was applied to"
        )

    def test_a_reply_stamped_before_its_root_still_renders_under_it(
        self, anchored, monkeypatch
    ):
        """``c["depth"]`` is a *sort* term, not only a rendering attribute.

        The depth **assignment** is well pinned -- break it and six tests turn
        red -- but until this test the depth **term in the sort key** was an
        unkilled mutant: delete ``c["depth"],`` from ``build_view_payload``'s key
        and all 446 tests at ``fcfba7d`` stay green (#488).

        It survived for the reason #482 named: every fixture in the suite runs
        its clock *with* ``AUTOINCREMENT``, and when the stamps agree with the
        ids a root always sorts ahead of its own replies on ``c["created_at"]``
        alone, so the depth term never has to do anything.  A backwards clock
        separates them.  ``create_comment`` binds whatever ``_now_iso()``
        returns into both stamps, so an NTP step -- or any wall clock that is not
        monotonic -- is enough to give a reply an earlier ``created_at`` than the
        root it hangs under, no DB surgery required.

        The symptom is not a reordering nit.  Without the depth term the payload
        emits the depth-1 card *above* the depth-0 card it is indented under:
        ``static/app.js`` renders the reply's indent against a root the reader
        has not met yet.  Hence the contract stated here: **a reply never renders
        above its own root, whatever the clock says.**

        Deliberately deaf to every other term.  Both comments are one thread, so
        ``root_of[...]``'s ``created_at`` and ``id`` are constant across the
        group; nothing is resolved, so ``created_at`` and ``updated_at`` are
        equal and the swap this class's other two tests catch cannot be what
        reddens this one; and the ``c["id"]`` tiebreak agrees with the wanted
        order.  Measured: green under all five of those mutations, red only when
        ``c["depth"],`` is deleted.

        Ablated: run the clock *forwards* instead and the mutant survives, so the
        inversion -- not the two-comment shape -- is the discriminator.
        """
        import db
        from server import build_view_payload

        clock = {"now": "2026-08-16T14:00:10+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root = _post_json_comment(anchored["client"], line_start=5, body="root")
        # The clock steps *backwards* before the reply is written.
        clock["now"] = "2026-08-16T14:00:05+00:00"
        reply = _post_json_comment(anchored["client"], line_start=5, body="reply")

        assert reply["parent_id"] == root["id"], (
            "fixture: #465's rule must have made the second comment a reply to "
            "the first, so that one is the other's root"
        )
        assert root["id"] < reply["id"], (
            "fixture: AUTOINCREMENT must give the later insert the higher id, "
            'so the c["id"] tiebreak *agrees* with the wanted order and cannot '
            "be what reddens this test"
        )
        stamps = _stamps_of(anchored["db"], [root["id"], reply["id"]])
        assert stamps[reply["id"]]["created_at"] < stamps[root["id"]]["created_at"], (
            "fixture: the reply must carry the *earlier* created_at -- that "
            "inversion is the whole discriminator; with the clocks running with "
            "the ids the depth term is redundant and the mutant survives"
        )
        assert stamps[root["id"]]["created_at"] == stamps[root["id"]]["updated_at"], (
            "fixture: nothing may be resolved here, or an updated_at divergence "
            "could red this test instead of the depth term"
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["depth"] for c in group] == [0, 1], (
            "a reply must never render above its own root: dropping "
            'c["depth"] from the sort key emits the depth-1 card first, so the '
            "reader sees a reply indented under a root that is not there yet"
        )
        assert [c["body"] for c in group] == ["root", "reply"]
        assert [c["id"] for c in group] == [root["id"], reply["id"]]

    def test_siblings_render_in_clock_order_not_insertion_order(
        self, anchored, monkeypatch
    ):
        """The within-thread tiebreak is the comment's *own* clock, and is a clock.

        The test two above pins ``c["created_at"]`` against
        ``c["created_at"]`` -> ``c["updated_at"]``.  Two mutations of the same
        term were left alive by it and measured surviving the whole suite at
        ``fcfba7d`` (#488): **deleting** the term, and **replacing** it with
        ``root_of[c["id"]]["created_at"]``.  Both are plausible "simplify the
        key" edits, and within a single thread they are the same mutation -- the
        root's stamp is constant across the group, so substituting it *is* the
        deletion.  One fixture therefore kills both.

        They survived because every other fixture lets ``c["id"]`` stand in for
        ``c["created_at"]``: with the clock running with ``AUTOINCREMENT`` the
        two terms give the same order, so dropping the first changes nothing.
        Here the clock runs **against** the ids -- the later-inserted sibling is
        stamped earlier -- and the terms disagree.  The contract: **siblings
        render by when they were written, not by the order the rows landed in.**

        This is the #482 hazard at reply level.  It is reachable without any
        clock skew at all: ids come from ``AUTOINCREMENT`` and stamps from
        ``_now_iso()``, two independent sources, and #482 already established
        that the read path must not assume they agree.

        Deaf to the neighbouring terms by construction.  All three comments are
        one route-built thread (#465 chains them), so both root terms are
        constant across the group; nothing is resolved, so the ``updated_at``
        swap cannot red it; and the root is stamped earliest *and* has the lowest
        id, so ``c["depth"]`` is not load-bearing either.  Measured: green under
        all four of those mutations, red under both of the two it targets.

        Ablated: with the clocks running *with* the ids both mutants survive, and
        with only one reply they survive too -- two siblings are the minimum that
        can be reordered relative to each other.
        """
        import db
        from server import build_view_payload

        clock = {"now": "2026-08-16T14:00:00+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root = _post_json_comment(anchored["client"], line_start=5, body="root")
        clock["now"] = "2026-08-16T14:00:20+00:00"
        late = _post_json_comment(anchored["client"], line_start=5, body="stamped late")
        # ... and the clock steps back before the third comment is written.
        clock["now"] = "2026-08-16T14:00:10+00:00"
        early = _post_json_comment(
            anchored["client"], line_start=5, body="stamped early"
        )

        assert (late["parent_id"], early["parent_id"]) == (root["id"], late["id"]), (
            "fixture: #465's rule must have chained all three into one thread, "
            "so that the root terms of the sort key are constant across them"
        )
        assert root["id"] < late["id"] < early["id"], (
            "fixture: AUTOINCREMENT must give the later inserts higher ids"
        )
        stamps = _stamps_of(anchored["db"], [root["id"], late["id"], early["id"]])
        assert (
            stamps[root["id"]]["created_at"]
            < stamps[early["id"]]["created_at"]
            < stamps[late["id"]]["created_at"]
        ), (
            "fixture: the two siblings' clocks must *oppose* their ids -- that "
            'disagreement is the discriminator; the root stays earliest so that '
            'c["depth"] cannot be what orders this group'
        )
        assert all(
            s["created_at"] == s["updated_at"] for s in stamps.values()
        ), (
            "fixture: nothing may be resolved here, or the updated_at swap the "
            "test above catches could red this one instead"
        )

        group = build_view_payload("doc.md")["comments_by_block"][5]
        assert [c["body"] for c in group] == ["root", "stamped early", "stamped late"], (
            "siblings order by their own created_at: deleting the tiebreak -- or "
            'replacing it with root_of[c["id"]]["created_at"], which is constant '
            "within a thread -- falls back to c[\"id\"] and renders them in "
            "insertion order instead"
        )
        assert [c["id"] for c in group] == [root["id"], early["id"], late["id"]]
        assert [c["depth"] for c in group] == [0, 1, 1], (
            "both replies must nest under the one root -- that is why the root "
            "terms above the tiebreak cannot be what orders this group"
        )

    def test_same_instant_replies_render_in_the_order_they_were_written(
        self, anchored, monkeypatch
    ):
        """The final ``c["id"]`` tiebreak, and it is *not* an equivalent mutant.

        The sixth and last term of ``build_view_payload``'s key was the one
        survivor left after #481/#483/#485/#487/#489: delete ``c["id"],`` and
        all 448 tests at ``887ae03`` stay green (#488).  It was parked as
        *probably equivalent* on the argument that by the sixth term the five
        above it have already separated everything, so the DB's own order
        carries through a stable ``sorted()`` either way -- and the objection
        raised against that argument was that ``list_comments_by_path``'s
        ``ORDER BY line_start, created_at`` (``db.py``) leaves **ties**
        unspecified, so there would be no defined order to assert against.

        Both are wrong in the same place.  Each assumes two comments that agree
        on the five leading terms must also agree on ``line_start``.  They need
        not: the first term is the *block*, and a block spans several lines --
        ``DOC_465_MULTILINE``'s paragraph covers L3-5.  Two replies of one
        thread, in one block, anchored to **different lines of that block** and
        sharing a ``created_at``, agree on group, root stamp, root id, depth and
        own stamp, and differ only on ``c["id"]``.

        So no tie is involved.  The DB separates these two rows on the
        ``line_start`` *column*, which ``ORDER BY`` fully specifies, and hands
        them back in an order that **contradicts their ids**; ``sorted()`` is
        stable, so without the final term that contradicted order is what
        renders.  The premise is asserted below through
        ``list_comments_by_path`` itself rather than assumed, so the test states
        what it depends on and does not rest on rowid order.

        The contract: **replies written in the same instant render in the order
        they were written, not in the order of the lines they happen to point
        at.**  A reply's line anchor says which text it answers; it is not a
        reading order for a conversation.  Same-instant is not exotic -- two
        replies land in one ``_now_iso()`` value whenever a client posts a pair
        back to back, and the CLI does.

        Deaf to the five terms above it by construction: all three comments are
        one route-built thread in one block (#465 chains them), so group and
        both root terms are constant; both replies are depth 1; and the two
        share one stamp, so neither ``c["created_at"]`` nor its ``updated_at``
        swap can order them.  Measured: green under all five of those mutations,
        red under deleting ``c["id"],`` and under replacing it with
        ``c["created_at"]``.

        Ablated on both of its elements, and each is load-bearing.  Point the
        two replies at the *same* line and the DB order agrees with their ids
        again: mutant survives.  Keep the opposed lines but give them
        *different* stamps and ``c["created_at"]`` separates them one term
        earlier: mutant survives.  It takes both -- one instant, two lines --
        to reach the sixth term at all.
        """
        import db
        from server import build_view_payload

        # A block spanning several lines is what lets two comments share a
        # group and still differ on line_start: L1, then L3-5, then L7.
        anchored["doc"].write_text(DOC_465_MULTILINE)

        clock = {"now": "2026-08-17T09:00:00+00:00"}
        monkeypatch.setattr(db, "_now_iso", lambda: clock["now"])

        root = _post_json_comment(anchored["client"], line_start=3, body="root")
        # Both replies are written while the clock reads the same instant, and
        # the later one points at the *earlier* line of the block.
        clock["now"] = "2026-08-17T09:00:10+00:00"
        first = _post_json_comment(
            anchored["client"], line_start=5, body="written first"
        )
        second = _post_json_comment(
            anchored["client"], line_start=3, body="written second"
        )

        assert (first["parent_id"], second["parent_id"]) == (root["id"], first["id"]), (
            "fixture: #465's rule must have chained all three into one thread, "
            "so that the group and both root terms are constant across them"
        )
        assert root["id"] < first["id"] < second["id"], (
            "fixture: AUTOINCREMENT must give the later inserts higher ids"
        )
        stamps = _stamps_of(anchored["db"], [root["id"], first["id"], second["id"]])
        assert (
            stamps[first["id"]]["created_at"] == stamps[second["id"]]["created_at"]
        ), (
            "fixture: the two replies must share one created_at -- that is what "
            'retires c["created_at"] as a separator and leaves c["id"] alone '
            "deciding their order"
        )
        assert (
            stamps[root["id"]]["created_at"] < stamps[first["id"]]["created_at"]
        ), (
            'fixture: the root stays earliest so that c["depth"] is not '
            "load-bearing here -- the test above is the one that pins depth"
        )
        assert all(s["created_at"] == s["updated_at"] for s in stamps.values()), (
            "fixture: nothing may be resolved here, or the updated_at swaps the "
            "other tests in this class catch could red this one instead"
        )

        conn = db.get_connection(str(anchored["db"]))
        try:
            returned = [c["id"] for c in db.list_comments_by_path(conn, "doc.md")]
        finally:
            conn.close()
        assert returned == [root["id"], second["id"], first["id"]], (
            "fixture: the rows must reach build_view_payload in an order that "
            "contradicts their ids.  ORDER BY line_start, created_at separates "
            "the two replies on the line_start *column* -- a specified "
            "comparison, not a tie -- and the later-written one points at the "
            'earlier line.  A stable sorted() keeps that order unless c["id"] '
            "overrides it, which is exactly what this test measures"
        )

        group = build_view_payload("doc.md")["comments_by_block"][3]
        assert [c["body"] for c in group] == [
            "root",
            "written first",
            "written second",
        ], (
            "same-instant replies render in the order they were written: "
            'dropping the final c["id"] tiebreak -- or replacing it with '
            'c["created_at"], which is equal across the pair -- lets the DB\'s '
            "line_start order stand and renders the second reply above the first"
        )
        assert [c["id"] for c in group] == [root["id"], first["id"], second["id"]]
        assert [c["depth"] for c in group] == [0, 1, 1], (
            "both replies must nest under the one root -- that is why the terms "
            "above the tiebreak cannot be what orders this group"
        )


def _stamps_of(db_path, comment_ids):
    """``{id: {"created_at": ..., "updated_at": ...}}`` read straight from the DB.

    The payload carries both stamps, but reading them from the row keeps the
    fixture premise independent of the code path under test.
    """
    from db import get_connection

    conn = get_connection(db_path)
    try:
        rows = {}
        for cid in comment_ids:
            row = conn.execute(
                "SELECT created_at, updated_at FROM comments WHERE id = ?", (cid,)
            ).fetchone()
            assert row is not None, f"fixture: comment {cid} must exist"
            rows[cid] = {"created_at": row[0], "updated_at": row[1]}
        return rows
    finally:
        conn.close()


def _is_resolved(db_path, comment_id):
    from db import get_connection

    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT resolved FROM comments WHERE id = ?", (comment_id,)
        ).fetchone()
        return None if row is None else bool(row[0])
    finally:
        conn.close()


class TestOutOfRangeCommentIdOnResolve:
    """An unusable ``comment_id`` on the resolve routes is a client error (#475).

    ``/comment/{comment_id}/resolve`` and ``/unresolve`` pass a client-supplied
    id straight into an ``UPDATE ... WHERE id = ?``.  SQLite's INTEGER is signed
    64-bit and the driver raises ``OverflowError`` rather than matching no row,
    so an id wider than that surfaced as a **500** -- the same
    client-error-as-a-server-error defect #470/#473 removed for ``parent_id``,
    at the route family that never got the same treatment.

    An id that is in range but names no comment is now refused too, as **item 2
    of #475** (owner decision, 2026-08-17: "so caller will know").  It used to
    redirect 303 -- success reported for an update that did not happen -- which
    the web UI cannot reach, because its buttons are rendered from real rows,
    but the CLI and any script posting to these routes can.

    The two refusals answer differently on purpose, and the difference is
    load-bearing rather than decorative:

    * **422** -- the id cannot name a row *at any time*, because it is outside
      the range SQLite will bind.  A caller sending one has a broken id, not a
      stale one.
    * **404** -- the id is usable but no comment has it *now*.  The natural
      answer for a path segment naming a resource, and the answer the sibling
      ``parent_id`` guard cannot give because ``parent_id`` is a body field of a
      create request (it answers 422 for both cases).

    Collapsing the two into one code would cost a real pin: ``2**63-1`` names no
    row, so it is *only* distinguishable from ``2**63`` while the two answers
    differ.  Under a single code ``test_the_largest_legal_id_is_not_rejected``
    below could not fail, and the upper bound would go unpinned at this call
    site exactly as ``TestSqliteIntegerBound`` records it being unpinned at the
    ``parent_id`` one.
    """

    def _resolve(self, client, comment_id):
        return client.post(
            f"/comment/{comment_id}/resolve",
            data={"path": "doc.md"},
            follow_redirects=False,
        )

    def _unresolve(self, client, comment_id):
        return client.post(
            f"/comment/{comment_id}/unresolve",
            data={"path": "doc.md"},
            follow_redirects=False,
        )

    # ── the defect: an unusable id must not be a 5xx ──────────────────

    def test_an_out_of_range_id_is_rejected_not_a_server_error(self, anchored):
        """``10**19`` is too wide to be any row's id, which is the caller's mistake."""
        resp = self._resolve(anchored["client"], 10**19)
        assert resp.status_code == 422, (
            "an id too large to be any row's id reached the UPDATE and raised "
            "OverflowError, surfacing as a 5xx and telling the caller nothing; "
            f"got {resp.status_code}"
        )

    def test_the_rejection_names_the_id(self, anchored):
        """Echo back what the caller sent so they can tell which id was wrong."""
        resp = self._resolve(anchored["client"], 10**19)
        assert str(10**19) in str(resp.json().get("detail", "")), (
            f"the message must name the offending id; got {resp.text!r}"
        )

    def test_unresolve_is_covered_too(self, anchored):
        """The two routes are the same defect; fixing only one leaves it reachable."""
        resp = self._unresolve(anchored["client"], 10**19)
        assert resp.status_code == 422, (
            "/unresolve takes the same client-supplied id into the same UPDATE, "
            f"so it must reject identically; got {resp.status_code}"
        )

    def test_an_id_below_the_floor_is_rejected_too(self, anchored):
        """Out of range downwards overflows just as readily as upwards."""
        resp = self._resolve(anchored["client"], -(2**63) - 1)
        assert resp.status_code == 422, (
            "one below the INTEGER floor also raises OverflowError in the "
            f"driver, so it must be rejected by the same rule; got {resp.status_code}"
        )

    def test_the_boundary_value_itself_is_rejected(self, anchored):
        """``2**63`` is the *first* value the driver refuses to bind.

        Pins the exact upper bound rather than an order of magnitude: an
        implementation using ``<= 2**63`` still 500s on precisely this input,
        and ``10**19`` is wide enough (~1.08x the bound) that it cannot see the
        off-by-one.  This is the deferred Minor from the #474 review, closed
        here for the route it applies to.
        """
        resp = self._resolve(anchored["client"], 2**63)
        assert resp.status_code == 422, (
            "2**63 is out of range by exactly one and must be rejected, not "
            f"reach the UPDATE and overflow; got {resp.status_code}"
        )

    # ── the other side: the check must not over-reject ────────────────

    def test_a_real_comment_still_resolves(self, anchored):
        """The whole point of the route still works, and really flips the row."""
        c = _post_json_comment(anchored["client"], line_start=5, body="resolve me")
        assert _is_resolved(anchored["db"], c["id"]) is False, "fixture assumption"

        resp = self._resolve(anchored["client"], c["id"])
        assert resp.status_code == 303, f"got {resp.status_code} {resp.text!r}"
        assert _is_resolved(anchored["db"], c["id"]) is True, (
            "the redirect must mean the row was actually resolved"
        )

        assert self._unresolve(anchored["client"], c["id"]).status_code == 303
        assert _is_resolved(anchored["db"], c["id"]) is False, (
            "unresolve must flip it back, not merely redirect"
        )

    def test_the_largest_legal_id_is_not_rejected(self, anchored):
        """``2**63-1`` binds fine, so the *range* guard must let it through.

        The companion to the boundary test above: together they pin the bound
        from both sides, so neither widening nor narrowing it by one survives.

        It names no row, so since item 2 the answer is 404 rather than the old
        303 -- but what this test is about is that it is **not 422**.  Widening
        the range check to ``<= 2**63`` swaps 404 for 422 here and nowhere else,
        so this is the assertion that sees it.
        """
        resp = self._resolve(anchored["client"], 2**63 - 1)
        assert resp.status_code == 404, (
            "2**63-1 is a legal SQLite INTEGER, so it must get past the range "
            "check and be answered as an id naming no comment (404), not "
            f"refused as unusable (422); got {resp.status_code}"
        )

    def test_the_lowest_legal_id_is_not_rejected(self, anchored):
        """``-(2**63)`` binds too -- the floor is inclusive.

        Same shape as its twin above: 404 is the range check letting it past,
        422 would be the floor having been narrowed by one.
        """
        resp = self._resolve(anchored["client"], -(2**63))
        assert resp.status_code == 404, (
            "-(2**63) is inside the driver's range, so the lower bound must be "
            "inclusive and the id must reach the lookup that finds no row "
            f"(404), not be refused as unusable (422); got {resp.status_code}"
        )

    # ── item 2: an in-range id that names no comment ──────────────────

    def test_an_unknown_id_is_reported_not_answered_as_success(self, anchored):
        """Item 2 of #475: a 303 here is a success reported for a no-op.

        ``UPDATE ... WHERE id = 99999`` matches nothing and commits happily, so
        the route redirected exactly as it does after a real resolve.  Nothing
        about the response told the caller the comment was not there.
        """
        resp = self._resolve(anchored["client"], 99999)
        assert resp.status_code == 404, (
            "an id naming no comment must be reported to the caller, not "
            f"answered with the same 303 a successful resolve gets; got "
            f"{resp.status_code}"
        )

    def test_the_unknown_rejection_names_the_id(self, anchored):
        """Echo the id back, as the out-of-range refusal already does."""
        resp = self._resolve(anchored["client"], 99999)
        assert "99999" in str(resp.json().get("detail", "")), (
            f"the message must name the id that found no comment; got {resp.text!r}"
        )

    def test_unresolve_reports_an_unknown_id_too(self, anchored):
        """Both routes run the same UPDATE, so both no-opped the same way."""
        resp = self._unresolve(anchored["client"], 99999)
        assert resp.status_code == 404, (
            "/unresolve silently no-ops on an unknown id identically to "
            f"/resolve, so it must report it identically; got {resp.status_code}"
        )

    def test_a_negative_in_range_id_is_reported_as_unknown(self, anchored):
        """``-5`` binds fine and names nothing -- unknown, not unusable.

        Pins which guard owns it.  A negative id is in range, so answering it
        422 would mean the *range* check had grown a sign test it has no
        business having -- ``-(2**63)..-1`` are legal SQLite INTEGERs and
        ``test_a_parent_at_the_floor_is_accepted_when_it_names_a_real_row``
        shows a row can actually live there.
        """
        resp = self._resolve(anchored["client"], -5)
        assert resp.status_code == 404, (
            "-5 is a bindable id that names no comment, so it is the "
            f"not-found case, not the unusable-id case (422); got {resp.status_code}"
        )

    def test_an_out_of_range_id_keeps_its_own_answer(self, anchored):
        """Precedence: the range check runs before the lookup, and says so.

        Both refusals are now 4xx, so a single ``get_comment() is None`` guard
        placed *ahead* of the range check would leave the suite green on every
        rejection test in this class except this one -- and it would be a
        regression, because the lookup is what raises ``OverflowError``.
        """
        assert self._resolve(anchored["client"], 10**19).status_code == 422, (
            "an id too wide to bind must still be refused as unusable before "
            "it reaches the lookup, not reported as merely not found"
        )

    def test_an_unknown_id_leaves_the_other_comments_alone(self, anchored):
        """The refusal must not be a partial write dressed up as an error.

        ``resolve_comment()`` issues its ``UPDATE`` before it looks the row up,
        so a guard written on its return value reports 404 *after* a statement
        has already run and committed.  That is safe only because the statement
        matched nothing -- this asserts it, rather than assuming a ``WHERE``
        clause that no test reads.
        """
        c = _post_json_comment(anchored["client"], line_start=5, body="untouched")
        assert self._resolve(anchored["client"], 99999).status_code == 404
        assert _is_resolved(anchored["db"], c["id"]) is False, (
            "resolving a nonexistent id must not flip an unrelated comment"
        )

    @pytest.mark.parametrize("action", ["resolve", "unresolve"])
    def test_a_bad_path_is_still_reported_before_the_id(self, anchored, action):
        """Path validation keeps precedence; the id check must not jump ahead of it.

        Parametrised over both routes (#477 item 1).  Covering ``/resolve`` alone
        left the order unpinned on ``/unresolve``: moving the id check ahead of
        ``_resolve_file()`` on that route and nowhere else passed the whole
        suite, so a traversal attempt there could start being answered 422 --
        reporting the id rather than the traversal -- with nothing going red.
        """
        resp = anchored["client"].post(
            f"/comment/{10**19}/{action}",
            data={"path": "../outside.md"},
            follow_redirects=False,
        )
        assert resp.status_code == 403, (
            f"a traversal attempt on /{action} must still be refused as a "
            "traversal attempt, whatever id rides along with it; got "
            f"{resp.status_code}"
        )


# ── Disambiguating identical blocks (#467, #465 Phase 1.5) ──────────────


# Two byte-identical paragraphs, each preceded by a different one.  The
# preceding paragraph is the only thing in the document that tells the copies
# apart, which is what makes it usable as a disambiguator.
DUP_DOC = "# Title\n\nalpha\n\ndup para\n\nbeta\n\ndup para\n\ngamma\n"
# 1 '# Title', 3 'alpha', 5 'dup para', 7 'beta', 9 'dup para', 11 'gamma'

# A Marp deck: `---` slide separators are the real instance of a document that
# genuinely contains many byte-identical blocks, and adding a slide is the
# common edit.
MARP_DECK = (
    "---\nmarp: true\n---\n\n"
    "# Slide A\n\nbody a\n\n"
    "---\n\n"
    "# Slide B\n\nbody b\n\n"
    "---\n\n"
    "# Slide C\n\nbody c\n"
)
# 9 '---' (before Slide B), 15 '---' (before Slide C)


@pytest.fixture
def dup_git():
    """A git repo whose committed doc holds two identical paragraphs.

    Git-backed on purpose.  ``TestBlockIdResolution``'s ``anchored`` fixture is
    a plain ``tmp_path``, so the blame migration is a silent no-op there and the
    interaction that actually decides where a comment lands — blame moving the
    stored line, then a block-id match overriding it — cannot be observed at
    all.  Every edit below is committed for the same reason.
    """
    with tempfile.TemporaryDirectory() as td:
        doc = Path(td) / "doc.md"
        doc.write_text(DUP_DOC)
        _git(td, "init", "-q")
        _git(td, "config", "user.email", "test@example.com")
        _git(td, "config", "user.name", "Test")
        _git(td, "add", "doc.md")
        _git(td, "commit", "-qm", "initial doc")
        configure(td, Path(td) / "comments.db")
        yield {"dir": Path(td), "doc": doc, "client": TestClient(app)}


def _commit_edit(env, text, message="edit"):
    env["doc"].write_text(text)
    _git(env["dir"], "add", "doc.md")
    _git(env["dir"], "commit", "-qm", message)


def _placed(payload, comment_id):
    """The comment as the reader sees it, from the view payload.

    Read from ``comments_by_block`` rather than the stored row: the stored row
    is not where the bug lives — block-id resolution is display-only, so a row
    can be right while every reader sees the comment on the wrong copy.
    """
    c = _find(payload, comment_id)
    assert c is not None, "a comment must never be dropped from the view"
    return c


def _source_line(payload, number):
    """The 1-based source line *number* as served in this payload."""
    return payload["source"].split("\n")[number - 1]


class TestDuplicateBlockDisambiguation:
    """Which of several identical blocks a comment belongs to (#467).

    Identical blocks are the weak case for a content-derived id: the text says
    nothing about which copy an author picked, so something outside the block
    has to break the tie.  Every assertion here checks the *line the comment
    lands on* and that the line holds the commented text — with duplicates the
    text check alone cannot fail, so it is the line number that discriminates.
    """

    def test_a_copy_inserted_above_does_not_steal_the_comments(self, dup_git):
        """Inserting a fresh copy above must not rebind the existing ones.

        The positional occurrence suffix renumbers every copy after the
        insertion point, so the comment written on copy one matched the *new*
        copy and the comment written on copy two matched copy one — each
        comment silently moved to a block its author never saw.
        """
        from server import build_view_payload

        first = _post_json_comment(
            dup_git["client"], line_start=5, body="on the copy after alpha"
        )
        second = _post_json_comment(
            dup_git["client"], line_start=9, body="on the copy after beta"
        )
        _commit_edit(
            dup_git,
            "# Title\n\ndup para\n\nalpha\n\ndup para\n\nbeta\n\ndup para\n\ngamma\n",
        )
        payload = build_view_payload("doc.md")

        # 3 is the new copy, 7 the one after 'alpha', 11 the one after 'beta'.
        assert _source_line(payload, 7) == "dup para"
        assert _source_line(payload, 11) == "dup para"
        assert _placed(payload, first["id"])["line_start"] == 7, (
            "the comment written on the copy after 'alpha' must stay on it"
        )
        assert _group_of(payload, first["id"]) == 7
        assert _placed(payload, first["id"])["detached"] is False
        assert _placed(payload, second["id"])["line_start"] == 11, (
            "the comment written on the copy after 'beta' must stay on it"
        )
        assert _group_of(payload, second["id"]) == 11

    def test_deleting_the_first_copy_keeps_the_survivors_comment_attached(
        self, dup_git
    ):
        """The survivor inherits `-1`, so the comment written on it lost its id.

        Nothing else changed for that comment: its text is still in the file,
        one block up.  It was reported detached only because the occurrence
        number of a *different* copy went away.
        """
        from server import build_view_payload

        _post_json_comment(dup_git["client"], line_start=5, body="on the first copy")
        second = _post_json_comment(
            dup_git["client"], line_start=9, body="on the copy after beta"
        )
        _commit_edit(dup_git, "# Title\n\nalpha\n\nbeta\n\ndup para\n\ngamma\n")
        payload = build_view_payload("doc.md")

        assert _source_line(payload, 7) == "dup para"
        assert _placed(payload, second["id"])["line_start"] == 7
        assert _placed(payload, second["id"])["detached"] is False, (
            "its own text is still in the file; deleting the other copy must "
            "not detach it"
        )
        assert _group_of(payload, second["id"]) == 7

    def test_the_last_block_holding_the_text_wins_even_if_nothing_else_matches(
        self, dup_git
    ):
        """One block with the text is the answer, whatever else moved.

        Here a single commit deletes the first copy *and* rewords the paragraph
        before the second, so the comment's occurrence number and its context are
        both wrong — and its text is still in the file, once.  Both anchors are
        derived; the text is the thing the comment was written about.  Requiring
        the context to agree even when there is nothing to disambiguate would
        detach this comment on the strength of an edit to somebody else's
        paragraph.
        """
        from server import build_view_payload

        second = _post_json_comment(
            dup_git["client"], line_start=9, body="on the copy after beta"
        )
        _commit_edit(
            dup_git,
            "# Title\n\nalpha\n\nbeta, reworded\n\ndup para\n\ngamma\n",
            "delete the first copy and reword the context of the second",
        )
        payload = build_view_payload("doc.md")

        assert _source_line(payload, 7) == "dup para"
        assert _placed(payload, second["id"])["line_start"] == 7
        assert _placed(payload, second["id"])["detached"] is False

    def test_a_marp_separator_keeps_the_slide_it_was_written_on(self, dup_git):
        """A deck full of `---` is the real instance of this (#467).

        Every slide separator normalizes to the same three characters, so the
        occurrence number is all that tells them apart — and adding a slide at
        the front renumbers all of them at once.
        """
        from server import build_view_payload

        _commit_edit(dup_git, MARP_DECK, "deck")
        before_b = _post_json_comment(
            dup_git["client"], line_start=9, body="separator before Slide B"
        )
        before_c = _post_json_comment(
            dup_git["client"], line_start=15, body="separator before Slide C"
        )
        _commit_edit(
            dup_git,
            "---\nmarp: true\n---\n\n"
            "# Slide Z\n\nbody z\n\n"
            "---\n\n"
            "# Slide A\n\nbody a\n\n"
            "---\n\n"
            "# Slide B\n\nbody b\n\n"
            "---\n\n"
            "# Slide C\n\nbody c\n",
            "insert a slide at the front",
        )
        payload = build_view_payload("doc.md")

        # Separators are now at 9 (before A), 15 (before B) and 21 (before C).
        assert _source_line(payload, 15) == "---"
        assert _source_line(payload, 21) == "---"
        assert _placed(payload, before_b["id"])["line_start"] == 15, (
            "the comment on the separator before Slide B must still be on it, "
            "not on the separator that now precedes Slide A"
        )
        assert _placed(payload, before_b["id"])["detached"] is False
        assert _placed(payload, before_c["id"])["line_start"] == 21

    def test_the_deleted_copys_comment_lands_on_the_surviving_copy(self, dup_git):
        """The half of the delete case that is deliberately *not* changed.

        When one copy is left, a comment written on the copy that is gone still
        attaches to it.  That is the bounded-damage bargain #467 describes: the
        two blocks were byte-identical, so the reader is never shown text the
        comment was not written about — only a different instance of the same
        text.  Refusing the match instead would detach a comment whose text is
        demonstrably still in the file, which is the worse trade.
        """
        from server import build_view_payload

        first = _post_json_comment(
            dup_git["client"], line_start=5, body="on the first copy"
        )
        _commit_edit(dup_git, "# Title\n\nalpha\n\nbeta\n\ndup para\n\ngamma\n")
        payload = build_view_payload("doc.md")

        assert _source_line(payload, 7) == "dup para"
        assert _placed(payload, first["id"])["line_start"] == 7
        assert _placed(payload, first["id"])["detached"] is False

    def test_editing_the_context_hands_the_copies_back_to_the_ordinal(
        self, dup_git
    ):
        """The failure mode this disambiguator introduces, pinned deliberately.

        The tie is broken by the nearest preceding *different* block, so editing
        that block is what costs a duplicate its identity — a comment can be
        rebound by an edit that is not to its own text.  Here 'alpha' is
        reworded in the same commit that inserts a copy at the front: the stored
        context matches nothing, resolution falls back to the occurrence number,
        and the comment lands on the new copy exactly as it did before #467.

        The damage stays bounded — the line it lands on holds the same text —
        and it is confined to duplicates: a block whose text is unique in the
        document is matched on its digest alone, so no edit to its neighbours
        can move it (see the test below).
        """
        from server import build_view_payload

        first = _post_json_comment(
            dup_git["client"], line_start=5, body="on the copy after alpha"
        )
        _commit_edit(
            dup_git,
            "# Title\n\ndup para\n\nalpha, reworded\n\ndup para\n\nbeta\n\n"
            "dup para\n\ngamma\n",
            "reword the context and insert a copy above",
        )
        payload = build_view_payload("doc.md")

        assert _placed(payload, first["id"])["line_start"] == 3, (
            "with its context gone the comment is placed by occurrence number "
            "again — the known cost of a context-derived tiebreak"
        )
        assert _source_line(payload, 3) == "dup para", (
            "and the cost stays bounded: identical text, never other text"
        )

    def test_a_unique_blocks_comment_survives_an_edit_to_the_block_above(
        self, dup_git
    ):
        """Context is a tiebreak, not part of the identity.

        This is why the disambiguator is not simply folded into `block_id`.  A
        context-derived id would move whenever the surrounding text moved, so
        rewording one paragraph would detach the comments on the next one —
        which is a far more common edit than reordering identical blocks, and a
        regression against what master already guarantees.

        The edit is to 'alpha's *immediate* predecessor, which is what makes the
        test discriminate: rewording some other block leaves 'alpha's context
        untouched, so it would pass even if the context were being consulted.
        """
        from server import build_view_payload

        created = _post_json_comment(
            dup_git["client"], line_start=3, body="on alpha"
        )
        _commit_edit(
            dup_git,
            "# Title, reworded\n\nalpha\n\ndup para\n\nbeta\n\ndup para\n\ngamma\n",
            "reword the block immediately above",
        )
        payload = build_view_payload("doc.md")

        assert _placed(payload, created["id"])["detached"] is False, (
            "'alpha' is unique in the document, so its text alone identifies it "
            "— consulting its context here would detach it for an edit that was "
            "not to it"
        )
        placed = _placed(payload, created["id"])["line_start"]
        assert _source_line(payload, placed) == "alpha"

    def test_deleting_the_copy_above_keeps_the_run_tails_comment_attached(
        self, dup_git
    ):
        """The reader-visible half of the identical-neighbour skip (#494 item 3).

        A run of adjacent copies shares one context — the nearest block above
        whose text *differs* — rather than each copy taking its immediate
        predecessor.  Until now that skip was pinned only by a
        ``render_markdown_blocks()`` unit test, on the block dicts; what it costs
        a reader was unpinned.

        Here the comment is on the *tail* of a two-copy run and the commit
        deletes the copy above it.  Nothing about the commented paragraph
        changed: its text is still in the file.  If its context had been taken
        from the identical copy above, that context would now name a block that
        no longer exists, the digest would match two copies with neither
        matching the stored context, and resolution would fall through to the
        stored occurrence number — which the deletion has just retired.  The
        comment is then reported *detached*: the reader is told the paragraph it
        was written about is gone from a document that still contains it.

        Skipping the identical neighbour is what keeps the stored context
        pointing at 'alpha', which still exists and still names exactly one of
        the two surviving copies.
        """
        from server import build_view_payload

        _commit_edit(
            dup_git,
            "# Title\n\ndup para\n\nalpha\n\ndup para\n\ndup para\n\ngamma\n",
            "a document with an adjacent run of copies",
        )
        # 3 'dup para', 5 'alpha', 7 and 9 the run, 11 'gamma'.
        tail = _post_json_comment(
            dup_git["client"], line_start=9, body="on the second copy of the run"
        )
        _commit_edit(
            dup_git,
            "# Title\n\ndup para\n\nalpha\n\ndup para\n\ngamma\n",
            "delete the copy above the commented one",
        )
        payload = build_view_payload("doc.md")

        # The commented copy is the one that survives, now at 7; the unrelated
        # copy at the top of the document is still at 3.
        assert _source_line(payload, 3) == "dup para"
        assert _source_line(payload, 7) == "dup para"
        assert _placed(payload, tail["id"])["detached"] is False, (
            "the commented paragraph is still in the file — deleting the "
            "identical copy above it must not report it as lost"
        )
        assert _placed(payload, tail["id"])["line_start"] == 7, (
            "the surviving copy of the run is the one the comment was written "
            "on; the copy at the top of the document is a different block"
        )
        assert _group_of(payload, tail["id"]) == 7

    def test_a_backfilled_legacy_row_learns_a_context_too(self, dup_git):
        """The id a pre-#467 row is given is only as good as its context.

        A row that predates block ids is handed one on its first clean-tree
        view.  Handing it the id without the context it was resolved against
        leaves it exactly as ambiguous as before — it would be placed by
        occurrence number for the rest of its life, on a document that has
        already shown it holds identical blocks.
        """
        from db import create_comment, get_comment, get_connection
        from renderer import render_markdown_blocks
        from server import build_view_payload

        db_path = dup_git["dir"] / "comments.db"
        conn = get_connection(db_path)
        row = create_comment(
            conn,
            file_id="fid",
            line_start=9,
            line_end=9,
            author="legacy",
            body="pre-#465 comment on the copy after beta",
            file_path="doc.md",
        )
        conn.close()
        assert row["block_id"] is None and row["block_context"] is None

        build_view_payload("doc.md")

        conn = get_connection(db_path)
        stored = get_comment(conn, row["id"])
        conn.close()
        assert stored["block_id"] is not None
        # The exact digest, not `is not None` (#494 item 4): "" is a real value
        # here — it is what a block at the top of the document is given — so a
        # not-None assertion passes just as happily on a context that was
        # defaulted as on one that was computed, which is the whole distinction
        # this test exists to draw.  Expected value is read off `block_id`,
        # whose digest is the block's text, so it stays honest if the context
        # itself is broken.
        beta = next(
            b
            for b in render_markdown_blocks(DUP_DOC)
            if b["raw"].strip() == "beta"
        )
        assert stored["block_context"] == beta["block_id"].rsplit("-", 1)[0], (
            "the backfilled context must be the digest of 'beta', the nearest "
            "preceding block whose text differs — not a default"
        )

        _commit_edit(
            dup_git,
            "# Title\n\ndup para\n\nalpha\n\ndup para\n\nbeta\n\ndup para\n\ngamma\n",
            "insert a copy at the front",
        )
        payload = build_view_payload("doc.md")
        assert _source_line(payload, 11) == "dup para"
        assert _placed(payload, row["id"])["line_start"] == 11, (
            "the backfilled context must keep naming the copy after 'beta'"
        )


class TestReadSurfacesAgree:
    """Every read surface must name the same line for the same comment (#468).

    ``_resolve_comment_blocks()`` used to be reachable only through
    ``build_view_payload()``, so block-id resolution ran for the web view and
    for nothing else.  ``GET /api/comments`` returned the *stored* line, which
    on a dirty tree is the line the text sat at before the uncommitted edit —
    a different line, with different text, for the same comment id.  Agents
    read comments through ``comments_cli.py``, i.e. over this endpoint, so the
    consumer most likely to act on a comment was the one being told the wrong
    place.
    """

    def _api(self, client, comment_id):
        listed = client.get("/api/comments?path=doc.md").json()
        match = [c for c in listed if c["id"] == comment_id]
        assert match, f"comment {comment_id} missing from /api/comments"
        return match[0]

    def test_api_names_the_line_the_view_names(self, git_client, git_source_dir):
        """The disagreement itself: same DB, same dirty file, same comment.

        Asserted on the *response body*, not on the DB row — the row is what
        both surfaces already agreed on and is not where the bug lived.
        """
        created = _post_json_comment(git_client, line_start=5, body="on para two")
        assert created["line_start"] == 5, "written against the clean tree"

        moved = "preamble\n\n" + GIT_DOC  # uncommitted: 'para two' L5 -> L7
        (Path(git_source_dir) / "doc.md").write_text(moved)

        from server import build_view_payload

        view_line = _placed(build_view_payload("doc.md"), created["id"])["line_start"]
        api_line = self._api(git_client, created["id"])["line_start"]

        source = moved.splitlines()
        assert source[view_line - 1] == "para two", (
            "guard: the view must itself be right before agreement means anything"
        )
        assert source[api_line - 1] == "para two", (
            f"the API points at {source[api_line - 1]!r}, not the commented text"
        )
        assert api_line == view_line

    def test_api_reports_detached_when_the_text_is_gone(
        self, git_client, git_source_dir
    ):
        """A lost anchor must be visible to the API reader, not just the view.

        Without ``detached`` the endpoint hands back a line number that looks
        exactly as trustworthy as a resolved one while naming unrelated text.
        """
        created = _post_json_comment(git_client, line_start=5, body="on para two")
        (Path(git_source_dir) / "doc.md").write_text(
            "# Title\n\npara one\n\npara three\n"  # 'para two' deleted outright
        )

        assert self._api(git_client, created["id"])["detached"] is True

    def test_repeated_reads_do_not_keep_rewriting_the_row(
        self, git_client, git_source_dir
    ):
        """Sharing resolution lets a GET persist; it must settle after one.

        Resolution's blame step owns the stored anchor (#406) and writes it, so
        the endpoint is not side-effect free.  That is tolerable only if it is
        idempotent: the second GET must find nothing left to do.
        """
        from db import get_comment, get_connection

        created = _post_json_comment(git_client, line_start=5, body="on para two")
        doc = Path(git_source_dir) / "doc.md"
        doc.write_text("preamble\n\n" + GIT_DOC)
        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "insert a preamble")

        def stored():
            conn = get_connection(Path(git_source_dir) / "test_comments.db")
            row = dict(get_comment(conn, created["id"]))
            conn.close()
            return row

        self._api(git_client, created["id"])
        after_first = stored()
        self._api(git_client, created["id"])
        assert stored() == after_first, "a second GET must be a no-op on the row"
        assert after_first["line_start"] == 7, "blame relocated the anchor"

    def test_a_deleted_range_is_clamped_to_the_file_the_api_read(
        self, git_client, git_source_dir
    ):
        """The one placement argument the API *persists*, pinned end to end.

        When a committed edit deletes a comment's whole range, blame has no
        surviving line to offer, so the anchor is clamped to the file's current
        length — and then written back (``_migrate_comment_anchors()``).  The
        ``total_lines`` the endpoint passes therefore does not merely shape a
        response: it decides a *stored* anchor, for every later reader of both
        surfaces.  Sharing ``_placed_comments()`` makes the two surfaces run the
        same code; only an assertion makes them hand it the same file.

        The comment goes on the last paragraph and that paragraph is deleted, so
        the clamp lands on the final line and the placed line *is* the file
        length — a total that is too small or too large both show up here.
        """
        from db import get_comment, get_connection
        from server import build_view_payload

        created = _post_json_comment(git_client, line_start=7, body="on para three")

        remaining = "# Title\n\npara one\n\npara two\n"  # 'para three' gone
        (Path(git_source_dir) / "doc.md").write_text(remaining)
        _git(git_source_dir, "add", "doc.md")
        _git(git_source_dir, "commit", "-qm", "delete para three")

        source = remaining.splitlines()
        api = self._api(git_client, created["id"])
        assert api["line_start"] == len(source), (
            f"placed at L{api['line_start']} of a {len(source)}-line file — "
            "the clamp did not measure the file the endpoint read"
        )
        assert source[api["line_start"] - 1] == "para two", (
            "the clamp must land on the last line still on disk"
        )

        conn = get_connection(Path(git_source_dir) / "test_comments.db")
        row = dict(get_comment(conn, created["id"]))
        conn.close()
        assert row["line_start"] == len(source), (
            "the GET persisted this anchor, so a mismeasured clamp poisons the "
            "row for every later reader — /view included"
        )

        view_line = _placed(build_view_payload("doc.md"), created["id"])["line_start"]
        assert view_line == api["line_start"]

    def test_the_listing_is_ordered_by_where_the_comments_now_are(
        self, git_client, git_source_dir
    ):
        """Placing without re-sorting would list them in the old file order.

        ``list_comments_by_path()`` returns rows ``ORDER BY line_start``, i.e.
        the *stored* order, which is the order the file used to be in.  Once
        the lines are placed, that ordering is stale: a reader walking the
        listing top to bottom would jump backwards through the file.
        """
        first = _post_json_comment(git_client, line_start=3, body="on para one")
        second = _post_json_comment(git_client, line_start=5, body="on para two")

        # Uncommitted swap: 'para two' L5 -> L3, 'para one' L3 -> L5.
        (Path(git_source_dir) / "doc.md").write_text(
            "# Title\n\npara two\n\npara one\n\npara three\n"
        )

        listed = git_client.get("/api/comments?path=doc.md").json()
        assert [c["id"] for c in listed] == [second["id"], first["id"]], (
            "the comment on 'para two' now comes first in the file"
        )
        assert [c["line_start"] for c in listed] == [3, 5]
