"""Tests for the comments_cli.py JSON API CLI (#435).

Uses a real FastAPI TestClient server on a random port to test the CLI's
HTTP interaction end-to-end.
"""

import argparse
import json
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest
import uvicorn

from server import app, configure


@pytest.fixture
def source_dir():
    with tempfile.TemporaryDirectory() as td:
        md_file = Path(td) / "test.md"
        md_file.write_text("# Hello\n\nTest content.\n")
        yield td


@pytest.fixture
def server_url(source_dir):
    """Start a real uvicorn server on an ephemeral port, yield its URL."""
    db_path = Path(source_dir) / "test_comments.db"
    configure(source_dir, db_path)

    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server to start
    import time
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "Server did not start"

    # Get the actual port
    sockets = server.servers[0].sockets
    port = sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=5)


class TestCliList:
    def test_list_no_comments(self, server_url):
        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "list", "--path", "test.md"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0
        assert "No comments" in result.stdout

    def test_list_json_output(self, server_url):
        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "--json", "list", "--path", "test.md"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)


class TestCliPost:
    def test_post_and_list_roundtrip(self, server_url, source_dir):
        from file_id import derive_file_id

        fid = derive_file_id(str(Path(source_dir) / "test.md"))

        # Post a comment
        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "post", "--path", "test.md", "--file-id", fid,
             "--line-start", "1", "--line-end", "1",
             "--body", "CLI roundtrip test", "--author", "cli-user"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0
        assert "Created comment" in result.stdout

        # List and verify it appears
        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "--json", "list", "--path", "test.md"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["body"] == "CLI roundtrip test"
        assert data[0]["author"] == "cli-user"


class TestCliPostAuthorDefault:
    """A CLI post with no ``--author`` must be recorded as ``overlord``.

    ``--author`` defaulted to ``None``, so the CLI left the field out of the
    payload and the server's own default won (``_CommentCreate.author =
    "anon"``).  Every agent comment posted without the flag therefore landed
    under ``anon``, indistinguishable from a stranger: the live DB holds
    ``overlord``x4 beside ``anon``x1, one identity split across two names.
    The CLI is the agent's surface, so ``overlord`` is the honest default
    there.
    """

    def _post(self, server_url, source_dir, body, extra=()):
        from file_id import derive_file_id

        fid = derive_file_id(str(Path(source_dir) / "test.md"))
        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "post", "--path", "test.md", "--file-id", fid,
             "--line-start", "1", "--line-end", "1", "--body", body,
             *extra],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0, result.stderr
        return int(result.stdout.split("#")[1].strip())

    def _authors(self, server_url):
        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "--json", "list", "--path", "test.md"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0, result.stderr
        return {c["id"]: c["author"] for c in json.loads(result.stdout)}

    def test_omitting_author_records_overlord(self, server_url, source_dir):
        cid = self._post(server_url, source_dir, "no author flag")
        assert self._authors(server_url)[cid] == "overlord"

    def test_an_explicit_author_still_wins(self, server_url, source_dir):
        """Pinned separately: hardcoding ``overlord`` would pass the row above.

        A human running the CLI must still be able to sign their own name, and
        ``test_post_and_list_roundtrip`` only covers the flag on a fresh DB.
        """
        cid = self._post(
            server_url, source_dir, "named author",
            extra=("--author", "chaos"),
        )
        assert self._authors(server_url)[cid] == "chaos"


class TestCliListDetachedTag:
    """`list`'s human-readable branch must say when a line is unreliable (#468 N2).

    Since `50a309d` every read surface places a comment against the file as it
    is now and reports ``detached`` when it could not be worked out.  The CLI
    receives that flag and, before this, printed ``L5`` either way — so an
    agent reading through the CLI, the consumer #495 was fixed for, saw an
    adrift comment as typographically identical to a placed one.

    Asserted on the *rendered* line rather than on ``--json``: the flag has
    been in the JSON since #495, so a test that reads the JSON passes against
    the bug it is meant to pin.
    """

    def _render(self, monkeypatch, capsys, comments):
        """Run `cmd_list`'s human-readable branch over *comments*, return stdout."""
        import comments_cli

        monkeypatch.setattr(comments_cli, "_get", lambda url: comments)
        args = argparse.Namespace(
            base_url="http://example.invalid", path="test.md", json=False,
        )
        comments_cli.cmd_list(args)
        return capsys.readouterr().out

    def _comment(self, **over):
        c = {
            "id": 1,
            "author": "alice",
            "line_start": 5,
            "line_end": 5,
            "created_at": "2026-01-01T00:00:00Z",
            "body": "a note",
            "resolved": 0,
            "parent_id": None,
            "detached": False,
        }
        c.update(over)
        return c

    def test_a_detached_comment_is_tagged(self, monkeypatch, capsys):
        out = self._render(
            monkeypatch, capsys, [self._comment(detached=True)]
        )
        header = out.splitlines()[0]
        # The whole line, not `"[DETACHED]" in out`: the tag has to land beside
        # the location it qualifies, and the rest of the line must not move.
        assert header == "#1 alice L5 [DETACHED]  2026-01-01T00:00"

    def test_a_placed_comment_is_not_tagged(self, monkeypatch, capsys):
        out = self._render(
            monkeypatch, capsys, [self._comment(detached=False)]
        )
        header = out.splitlines()[0]
        assert header == "#1 alice L5  2026-01-01T00:00"
        # Pinned separately: a tag printed unconditionally would satisfy the
        # test above and make the flag meaningless.
        assert "[DETACHED]" not in out

    def test_detached_and_resolved_compose(self, monkeypatch, capsys):
        out = self._render(
            monkeypatch, capsys, [self._comment(detached=True, resolved=1)]
        )
        header = out.splitlines()[0]
        assert header == "#1 alice L5 [DETACHED] [RESOLVED]  2026-01-01T00:00"

    def test_a_range_keeps_its_span_beside_the_tag(self, monkeypatch, capsys):
        out = self._render(
            monkeypatch, capsys,
            [self._comment(detached=True, line_start=5, line_end=7)],
        )
        header = out.splitlines()[0]
        assert header == "#1 alice L5-7 [DETACHED]  2026-01-01T00:00"

    def test_a_detached_reply_is_tagged_too(self, monkeypatch, capsys):
        """A reply is placed by the same loop, so it detaches the same way.

        Found by the independent review of #497: without this row the tag
        could be gated on ``not parent_id`` and the suite stayed green at 485,
        while a detached reply printed ``(reply)`` and no warning — N2
        verbatim, on the surface the item is about.
        """
        out = self._render(
            monkeypatch, capsys,
            [self._comment(detached=True, parent_id=4)],
        )
        header = out.splitlines()[0]
        assert header == "#1 alice L5 [DETACHED] (reply)  2026-01-01T00:00"

    def test_a_comment_the_server_detached_is_tagged_end_to_end(
        self, server_url, source_dir
    ):
        """The flag has to survive the API → CLI hop, not just the formatter.

        The rows above are hand-made dicts, so they prove the rendering and
        nothing about whether a real `detached` ever reaches it.  Here the
        *server* decides: a comment is anchored to a paragraph, the paragraph
        is then rewritten out of the file, and its stored `block_id` matches
        nothing.

        A reply is posted as well, because the claim that a reply detaches
        like a root is what makes gating the tag on ``not parent_id`` a real
        defect rather than an equivalent mutant — so it is measured against
        the server, not assumed.
        """
        from file_id import derive_file_id

        md = Path(source_dir) / "test.md"
        fid = derive_file_id(str(md))

        def post(body, parent=None):
            cmd = [sys.executable, "comments_cli.py", "--base-url", server_url,
                   "post", "--path", "test.md", "--file-id", fid,
                   "--line-start", "3", "--line-end", "3",
                   "--body", body, "--author", "cli-user"]
            if parent is not None:
                cmd += ["--parent-id", str(parent)]
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                cwd=str(Path(__file__).parent),
            )
            assert r.returncode == 0, r.stderr
            return int(r.stdout.split("#")[1].strip())

        root_id = post("about the paragraph")
        reply_id = post("answering that", parent=root_id)

        # The anchored paragraph is gone; the line it sat on still exists.
        md.write_text("# Hello\n\nSomething else entirely.\n")

        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "--json", "list", "--path", "test.md"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0, result.stderr
        # Guard against a vacuous pass: if the server stopped detaching these
        # comments the rendering assertions below would be checking nothing.
        by_id = {c["id"]: c for c in json.loads(result.stdout)}
        assert by_id[root_id]["detached"] is True
        assert by_id[reply_id]["detached"] is True
        assert by_id[reply_id]["parent_id"] == root_id

        result = subprocess.run(
            [sys.executable, "comments_cli.py", "--base-url", server_url,
             "list", "--path", "test.md"],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent),
        )
        assert result.returncode == 0, result.stderr
        headers = {
            line.split()[0]: line
            for line in result.stdout.splitlines() if line.startswith("#")
        }
        assert "[DETACHED]" in headers[f"#{root_id}"]
        assert "[DETACHED]" in headers[f"#{reply_id}"]
        assert "(reply)" in headers[f"#{reply_id}"]
