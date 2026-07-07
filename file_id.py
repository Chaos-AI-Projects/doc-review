"""Derive a stable file identifier for a reviewed document.

Primary: git blob object id (SHA-1 of the file content as git stores it).
Fallback: SHA-256 of (absolute path + file content) when the file is not
git-tracked.
"""

import hashlib
import subprocess
from pathlib import Path


def git_blob_id(path: str) -> str | None:
    """Return the git blob object id for *path*, or None if not git-tracked."""
    try:
        result = subprocess.run(
            ["git", "hash-object", path],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            blob_id = result.stdout.strip()
            # Verify the file is actually tracked by git (hash-object works on
            # any file, even untracked ones, but we only want tracked files).
            ls_result = subprocess.run(
                ["git", "ls-files", "--error-unmatch", path],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if ls_result.returncode == 0:
                return blob_id
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def content_hash_id(path: str) -> str:
    """Fallback: SHA-256 of absolute path + file content."""
    abs_path = str(Path(path).resolve())
    content = Path(path).read_bytes()
    h = hashlib.sha256()
    h.update(abs_path.encode())
    h.update(content)
    return h.hexdigest()


def derive_file_id(path: str) -> str:
    """Return a stable identifier for *path*.

    Uses the git blob object id when the file is tracked by git.
    Falls back to a path+content SHA-256 hash otherwise.
    """
    blob = git_blob_id(path)
    if blob is not None:
        return blob
    return content_hash_id(path)
