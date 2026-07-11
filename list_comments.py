#!/usr/bin/env python3
"""List a file's comments across all versions, relocating lines via git blame.

CLI usage:
    python list_comments.py --file PATH [--repo PATH] [--db PATH]
                            [--include-resolved | --open-only] [--json]

Reuses db.get_connection / db.list_comments and file_id helpers.
Does NOT touch server.py, renderer.py, or the DB schema.
"""

import argparse
import json as json_mod
import subprocess
from pathlib import Path

from db import get_connection, init_db, list_comments
from file_id import content_hash_id


def _run_git(repo: str, *args: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _enumerate_blob_ids(file_path: str, repo: str) -> dict[str, str | None]:
    """Build a mapping of blob_id -> commit_sha for every version of file_path.

    Includes the current working-tree blob and the content-hash fallback.
    The commit_sha is None for the working-tree blob and content-hash.
    """
    abs_file = str(Path(file_path).resolve())
    # Relative path from repo root for git commands
    try:
        rel_path = str(Path(abs_file).relative_to(Path(repo).resolve()))
    except ValueError:
        rel_path = abs_file

    blob_map: dict[str, str | None] = {}

    # 1. Current working-tree blob (git hash-object on the file as-is)
    result = _run_git(repo, "hash-object", abs_file)
    if result.returncode == 0 and result.stdout.strip():
        blob_map[result.stdout.strip()] = None  # None = current working tree

    # 2. Content-hash fallback (for untracked files)
    if Path(abs_file).exists():
        chash = content_hash_id(abs_file)
        blob_map[chash] = None

    # 3. Historical blobs from git log
    log_result = _run_git(repo, "log", "--format=%H", "--follow", "--", rel_path)
    if log_result.returncode == 0 and log_result.stdout.strip():
        commits = log_result.stdout.strip().split("\n")
        for commit in commits:
            rev_result = _run_git(repo, "rev-parse", f"{commit}:{rel_path}")
            if rev_result.returncode == 0 and rev_result.stdout.strip():
                blob_id = rev_result.stdout.strip()
                # Keep earliest commit for each blob (first seen in reverse-chrono)
                if blob_id not in blob_map:
                    blob_map[blob_id] = commit
                elif blob_map[blob_id] is None:
                    # Working-tree blob also exists in history — record the commit
                    blob_map[blob_id] = commit

    return blob_map


def _forward_map_lines(
    repo: str,
    rel_path: str,
    commit: str,
    line_start: int,
    line_end: int,
    head_sha: str,
) -> tuple[int | None, int | None, str]:
    """Forward-map a line range from an old commit to HEAD using git blame --reverse.

    Returns (current_start, current_end, status).
    status is 'mapped' on success, 'removed' if the lines no longer exist.
    """
    # Single blame call for the full range
    result = _run_git(
        repo,
        "blame", "--reverse", "-p",
        f"{commit}..HEAD",
        f"-L{line_start},{line_end}",
        "--", rel_path,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None, None, "removed"

    # Parse porcelain output — each blamed line starts with a header line:
    #   <sha> <orig_line> <final_line> [<num_lines>]
    # In --reverse mode:
    #   sha = last commit where the line existed
    #   orig_line = line number in HEAD (the END revision)
    #   final_line = line number in the START revision (commit)
    # If sha != HEAD, the line was deleted before HEAD.
    mapped_lines = []
    for output_line in result.stdout.split("\n"):
        parts = output_line.split()
        if len(parts) < 3:
            continue
        # Header lines start with a hex SHA (40 chars)
        if len(parts[0]) != 40:
            continue
        try:
            int(parts[1])
            int(parts[2])
        except ValueError:
            continue

        blame_sha = parts[0]
        if blame_sha != head_sha:
            # Line was deleted — last seen at blame_sha, not at HEAD
            continue
        mapped_lines.append(int(parts[1]))  # orig_line = line in HEAD

    if not mapped_lines:
        return None, None, "removed"

    return min(mapped_lines), max(mapped_lines), "mapped"


def collect_comments(
    file_path: str,
    repo: str,
    db_path: str,
    include_resolved: bool = True,
) -> list[dict]:
    """Collect all comments for a file across all versions, with line translation.

    Returns a list of comment dicts augmented with:
    - original_line_start, original_line_end: as stored in the DB
    - current_line_start, current_line_end: translated to current version (or None)
    - original_commit: short commit hash where the comment was anchored (or None)
    - status: 'current', 'mapped', or 'removed'
    - file_id: the blob/hash the comment was stored against
    """
    abs_file = str(Path(file_path).resolve())
    try:
        rel_path = str(Path(abs_file).relative_to(Path(repo).resolve()))
    except ValueError:
        rel_path = abs_file

    blob_map = _enumerate_blob_ids(file_path, repo)

    # Get current working-tree blob for comparison
    wt_result = _run_git(repo, "hash-object", abs_file)
    current_blob = wt_result.stdout.strip() if wt_result.returncode == 0 else None

    # Get HEAD sha once for all forward-map calls
    head_result = _run_git(repo, "rev-parse", "HEAD")
    head_sha = head_result.stdout.strip() if head_result.returncode == 0 else ""

    # Also get content-hash for current file
    current_content_hash = None
    if Path(abs_file).exists():
        current_content_hash = content_hash_id(abs_file)

    conn = get_connection(db_path)
    init_db(conn)

    results = []
    seen_ids = set()

    for blob_id, commit in blob_map.items():
        comments = list_comments(conn, blob_id, include_resolved=include_resolved)
        for c in comments:
            if c["id"] in seen_ids:
                continue
            seen_ids.add(c["id"])

            entry = {
                "id": c["id"],
                "file_id": c["file_id"],
                "author": c["author"],
                "body": c["body"],
                "resolved": c["resolved"],
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
                "parent_id": c["parent_id"],
                "original_line_start": c["line_start"],
                "original_line_end": c["line_end"],
                "original_commit": None,
                "current_line_start": None,
                "current_line_end": None,
                "status": "current",
            }

            is_current = (blob_id == current_blob or blob_id == current_content_hash)

            if is_current and commit is None:
                # Comment is on the current version — no translation needed
                entry["current_line_start"] = c["line_start"]
                entry["current_line_end"] = c["line_end"]
                entry["status"] = "current"
            elif commit is not None:
                # Comment is on an older version — forward-map via git blame
                entry["original_commit"] = commit[:8]
                if blob_id == current_blob:
                    # The blob matches current content but has a commit —
                    # no translation needed
                    entry["current_line_start"] = c["line_start"]
                    entry["current_line_end"] = c["line_end"]
                    entry["status"] = "current"
                else:
                    cur_start, cur_end, status = _forward_map_lines(
                        repo, rel_path, commit, c["line_start"], c["line_end"],
                        head_sha,
                    )
                    entry["current_line_start"] = cur_start
                    entry["current_line_end"] = cur_end
                    entry["status"] = status
            else:
                # Content-hash only (untracked), treat as current
                entry["current_line_start"] = c["line_start"]
                entry["current_line_end"] = c["line_end"]
                entry["status"] = "current"

            results.append(entry)

    conn.close()

    # Sort by current line (removed comments at the end)
    results.sort(key=lambda x: (
        x["status"] == "removed",
        x["current_line_start"] or 0,
        x["id"],
    ))

    return results


def format_text(results: list[dict], file_path: str) -> str:
    """Format results as human-readable text."""
    if not results:
        return f"No comments found for {file_path}"

    lines = [f"Comments for {file_path}", "=" * 60]
    current_group = None

    for c in results:
        if c["status"] == "removed":
            loc = "REMOVED (line deleted)"
        else:
            if c["current_line_start"] == c["current_line_end"]:
                loc = f"L{c['current_line_start']}"
            else:
                loc = f"L{c['current_line_start']}-{c['current_line_end']}"

        if loc != current_group:
            current_group = loc
            lines.append("")
            lines.append(f"--- {loc} ---")

        origin = ""
        if c["original_commit"]:
            if c["original_line_start"] == c["original_line_end"]:
                origin = f" (was L{c['original_line_start']} @ {c['original_commit']})"
            else:
                origin = f" (was L{c['original_line_start']}-{c['original_line_end']} @ {c['original_commit']})"

        resolved_tag = " [RESOLVED]" if c["resolved"] else ""
        lines.append(f"  #{c['id']} by {c['author']}{resolved_tag}{origin}")
        # Indent body
        for body_line in c["body"].split("\n"):
            lines.append(f"    {body_line}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="List a file's comments across all versions, "
                    "relocating lines via git blame."
    )
    parser.add_argument("--file", required=True, help="Path to the file")
    parser.add_argument("--repo", default=None,
                        help="Path to the git repo (default: auto-detect)")
    parser.add_argument("--db", default="comments.db",
                        help="Path to comments.db (default: comments.db)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--include-resolved", action="store_true", default=True,
                       help="Include resolved comments (default)")
    group.add_argument("--open-only", action="store_true",
                       help="Show only unresolved comments")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    args = parser.parse_args()

    file_path = str(Path(args.file).resolve())

    # Auto-detect repo if not specified
    if args.repo:
        repo = str(Path(args.repo).resolve())
    else:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(Path(file_path).parent),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            repo = result.stdout.strip()
        else:
            repo = str(Path.cwd())

    include_resolved = not args.open_only

    results = collect_comments(
        file_path=file_path,
        repo=repo,
        db_path=args.db,
        include_resolved=include_resolved,
    )

    if args.json:
        print(json_mod.dumps(results, indent=2))
    else:
        print(format_text(results, args.file))


if __name__ == "__main__":
    main()
