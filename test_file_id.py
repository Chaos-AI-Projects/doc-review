"""Tests for file_id derivation."""

import tempfile
from pathlib import Path

from file_id import content_hash_id, derive_file_id, git_blob_id


def test_git_blob_id_for_tracked_file():
    """A file tracked by git should return its blob object id."""
    # Use a file we know is in this repo
    repo_root = Path(__file__).resolve().parent.parent
    readme = repo_root / "CLAUDE.md"
    if readme.exists():
        blob = git_blob_id(str(readme))
        assert blob is not None
        # Git blob ids are 40-char hex strings
        assert len(blob) == 40
        assert all(c in "0123456789abcdef" for c in blob)


def test_git_blob_id_for_untracked_file():
    """An untracked temp file should return None."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
        f.write(b"not in git")
        f.flush()
        blob = git_blob_id(f.name)
    assert blob is None


def test_content_hash_id_deterministic():
    """content_hash_id should return the same hash for the same file."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("hello world")
        f.flush()
        h1 = content_hash_id(f.name)
        h2 = content_hash_id(f.name)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


def test_content_hash_id_differs_for_different_content():
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f1:
        f1.write("aaa")
        f1.flush()
        h1 = content_hash_id(f1.name)
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f2:
        f2.write("bbb")
        f2.flush()
        h2 = content_hash_id(f2.name)
    assert h1 != h2


def test_derive_file_id_uses_git_for_tracked():
    """derive_file_id should use git blob id for tracked files."""
    repo_root = Path(__file__).resolve().parent.parent
    readme = repo_root / "CLAUDE.md"
    if readme.exists():
        fid = derive_file_id(str(readme))
        # Should match git blob id
        expected = git_blob_id(str(readme))
        if expected:
            assert fid == expected


def test_derive_file_id_falls_back_for_untracked():
    """derive_file_id should use content hash for untracked files."""
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False, mode="w") as f:
        f.write("not tracked by git")
        f.flush()
        fid = derive_file_id(f.name)
    assert len(fid) == 64  # SHA-256 hex
