#!/usr/bin/env python3
"""Thin CLI for reading and posting doc-review comments via the JSON API.

Usage:
    # List comments for a file
    python comments_cli.py list --path doc.md [--base-url URL] [--json]

    # Post a comment
    python comments_cli.py post --path doc.md --file-id FID \
        --line-start 1 --line-end 1 --body "Comment text" \
        [--author NAME] [--parent-id ID] [--base-url URL]

Talks to the HTTP API (GET/POST /api/comments), not the DB directly.
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "http://127.0.0.1:28080"


def _get(url: str) -> dict | list:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())


def _post(url: str, data: dict) -> tuple[int, dict]:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode()) if e.fp else {}


def cmd_list(args: argparse.Namespace) -> None:
    url = f"{args.base_url}/api/comments?path={urllib.parse.quote(args.path)}"
    try:
        comments = _get(url)
    except urllib.error.HTTPError as e:
        print(f"Error: HTTP {e.code} — {e.reason}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Error: could not connect to {args.base_url} — {e.reason}",
              file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(comments, indent=2))
    else:
        if not comments:
            print(f"No comments for {args.path}")
            return
        for c in comments:
            loc = f"L{c['line_start']}"
            if c["line_end"] != c["line_start"]:
                loc += f"-{c['line_end']}"
            # Beside the location, because that is what it qualifies: the
            # server could not place this comment against the file as it is
            # now, so the line above is a stale guess (#468).  Without the
            # tag an adrift comment reads exactly like a placed one here,
            # while --json has carried the flag since #495 — and this is the
            # surface agents read through.
            detached = " [DETACHED]" if c.get("detached") else ""
            resolved = " [RESOLVED]" if c.get("resolved") else ""
            reply = " (reply)" if c.get("parent_id") else ""
            print(f"#{c['id']} {c['author']} {loc}{detached}{resolved}{reply}  "
                  f"{c['created_at'][:16]}")
            for line in c["body"].split("\n"):
                print(f"  {line}")
            print()


def cmd_post(args: argparse.Namespace) -> None:
    url = f"{args.base_url}/api/comments"
    payload = {
        "file_id": args.file_id,
        "path": args.path,
        "line_start": args.line_start,
        "line_end": args.line_end,
        "body": args.body,
    }
    if args.author:
        payload["author"] = args.author
    if args.parent_id:
        payload["parent_id"] = args.parent_id

    try:
        status, data = _post(url, payload)
    except urllib.error.URLError as e:
        print(f"Error: could not connect to {args.base_url} — {e.reason}",
              file=sys.stderr)
        sys.exit(1)

    if status == 201:
        print(f"Created comment #{data['id']}")
        if args.json:
            print(json.dumps(data, indent=2))
    else:
        print(f"Error: HTTP {status}", file=sys.stderr)
        print(json.dumps(data, indent=2), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read/post doc-review comments via the JSON API",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Server base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    lp = sub.add_parser("list", help="List comments for a file")
    lp.add_argument("--path", required=True, help="Relative file path")

    # post
    pp = sub.add_parser("post", help="Post a comment")
    pp.add_argument("--path", required=True, help="Relative file path")
    pp.add_argument("--file-id", required=True, help="File ID")
    pp.add_argument("--line-start", type=int, required=True)
    pp.add_argument("--line-end", type=int, required=True)
    pp.add_argument("--body", required=True, help="Comment body")
    pp.add_argument("--author", default=None, help="Author name")
    pp.add_argument("--parent-id", type=int, default=None,
                    help="Parent comment ID (for replies)")

    args = parser.parse_args()
    if args.command == "list":
        cmd_list(args)
    elif args.command == "post":
        cmd_post(args)


if __name__ == "__main__":
    main()
