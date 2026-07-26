"""Tests for the comments_cli.py JSON API CLI (#435).

Uses a real FastAPI TestClient server on a random port to test the CLI's
HTTP interaction end-to-end.
"""

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
