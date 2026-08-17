"""doc-review: line-anchored markdown review web app.

Usage:
    python server.py <path-to-file-or-directory>

Serves on 127.0.0.1:28080 by default (override with --host / --port).
"""

import argparse
import functools
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from db import (
    create_comment,
    get_comment,
    get_connection,
    init_db,
    latest_comment_in_block,
    list_comments,
    list_comments_by_path,
    resolve_comment,
    set_comment_block_id,
    unresolve_comment,
    update_comment_anchor,
)
from file_id import derive_file_id
from renderer import (
    extract_toc,
    normalized_offset,
    raw_offset,
    render_markdown_blocks,
)
from view_specs import header_fields, source_row_specs, toc_item_specs

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="doc-review")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _safe_tojson(value: object) -> Markup:
    """Serialize to JSON and escape </script> to prevent HTML parser breakout."""
    s = json.dumps(value, ensure_ascii=False)
    s = s.replace("<", "\\u003c")
    return Markup(s)


templates.env.filters["safe_tojson"] = _safe_tojson


@functools.cache
def static_version(filename: str) -> str:
    """Return an 8-char content hash for a file under static/.

    Results are cached for the process lifetime.  A server restart is
    required after deploying new static assets so the hashes update.
    """
    path = (BASE_DIR / "static" / filename).resolve()
    if not path.is_relative_to(BASE_DIR / "static"):
        raise ValueError(f"Illegal static filename: {filename}")
    return hashlib.sha256(path.read_bytes()).hexdigest()[:8]


templates.env.globals["static_version"] = static_version


@app.middleware("http")
async def add_static_cache_control(request: Request, call_next):
    """Add Cache-Control: no-cache to /static responses."""
    response = await call_next(request)
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response

# Globals set by configure()
_db_path: str = "comments.db"
_source_root: Path = Path(".")


def configure(source_root: str | Path, db_path: str | Path = "comments.db") -> None:
    """Set the source root and DB path.  Called before the server starts."""
    global _db_path, _source_root
    _source_root = Path(source_root).resolve()
    _db_path = str(db_path)
    conn = get_connection(_db_path)
    init_db(conn)
    conn.close()


def _conn():
    return get_connection(_db_path)


def _resolve_file(rel_path: str) -> Path:
    """Resolve *rel_path* against the source root, ensuring it stays inside."""
    target = (_source_root / rel_path).resolve()
    if not target.is_relative_to(_source_root):
        raise HTTPException(status_code=403, detail="Path traversal denied")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target


def _list_files(root: Path) -> list[str]:
    """Recursively list markdown files under *root* as relative paths."""
    exts = {".md", ".markdown"}
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            files.append(str(p.relative_to(root)))
    return files


# ── Git anchor helpers (#406) ───────────────────────────────────────────
#
# All git calls run against the repo containing the *served file* via
# `git -C <file dir>`, never the server process cwd (the cwd mistake was
# the exact bug documented in #404).

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)")


def _git_head_if_clean(target: Path) -> str | None:
    """Return HEAD of the repo containing *target* when the file is
    git-tracked and clean vs HEAD; otherwise None."""
    d = str(target.parent)
    name = target.name
    try:
        tracked = subprocess.run(
            # `--` ends option parsing: without it a name like `-x.md` is read
            # as the `-x` (exclude) flag, the pathspec vanishes, and
            # `--error-unmatch` succeeds for a file git has never seen.
            ["git", "-C", d, "ls-files", "--error-unmatch", "--", name],
            capture_output=True, timeout=5,
        )
        if tracked.returncode != 0:
            return None
        clean = subprocess.run(
            ["git", "-C", d, "diff", "--quiet", "HEAD", "--", name],
            capture_output=True, timeout=5,
        )
        if clean.returncode != 0:
            return None
        head = subprocess.run(
            ["git", "-C", d, "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if head.returncode != 0:
            return None
        return head.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _is_tracked_and_dirty(target: Path) -> bool:
    """True when *target* is git-tracked but differs from HEAD.

    Distinct from ``not _git_head_if_clean(target)``, which cannot tell a dirty
    file from one that git does not know about.  The difference matters for the
    block-id backfill: a dirty tracked file has a correction mechanism (the
    reverse-blame migration) that is being skipped, so its stored line carries
    no evidence and must not be turned into a permanent block id.  A file with
    no git behind it has no such mechanism to wait for.
    """
    d = str(target.parent)
    name = target.name
    try:
        tracked = subprocess.run(
            # `--` ends option parsing: without it a name like `-x.md` is read
            # as the `-x` (exclude) flag, the pathspec vanishes, and
            # `--error-unmatch` succeeds for a file git has never seen.
            ["git", "-C", d, "ls-files", "--error-unmatch", "--", name],
            capture_output=True, timeout=5,
        )
        if tracked.returncode != 0:
            return False
        head = subprocess.run(
            ["git", "-C", d, "rev-parse", "--verify", "-q", "HEAD"],
            capture_output=True, timeout=5,
        )
        if head.returncode != 0:
            # A repo with no commits: nothing to diff against, and nothing for
            # the blame migration to arrive from later either.
            return False
        clean = subprocess.run(
            ["git", "-C", d, "diff", "--quiet", "HEAD", "--", name],
            capture_output=True, timeout=5,
        )
        return clean.returncode != 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _blame_surviving_lines(
    target: Path, anchor_commit: str, head: str, line_start: int, line_end: int
) -> list[int] | None:
    """Map *line_start..line_end* (valid at *anchor_commit*) to the line
    numbers those lines occupy in *head*, via reverse blame.

    Returns the surviving line numbers in HEAD ([] when the whole range was
    deleted), or None when blame itself failed (caller should leave the
    stored anchor untouched).
    """
    try:
        result = subprocess.run(
            [
                "git", "-C", str(target.parent),
                "blame", "--reverse", "--porcelain",
                f"{anchor_commit}..HEAD",
                "-L", f"{line_start},{line_end}",
                "--", target.name,
            ],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if result.returncode != 0:
        return None
    survivors = []
    for line in result.stdout.splitlines():
        m = _BLAME_HEADER_RE.match(line)
        # Reverse blame attributes a line to the last commit in which it
        # existed; lines surviving to HEAD are attributed to HEAD itself,
        # with the header's second field being the line number in HEAD.
        if m and m.group(1) == head:
            survivors.append(int(m.group(2)))
    return survivors


def _migrate_comment_anchors(
    conn, comments: list[dict], target: Path, total_lines: int
) -> None:
    """Re-anchor comments whose anchor_commit lags the file's current HEAD.

    Mutates *comments* in place and persists updates, so blame runs once per
    edit rather than on every view.  Skipped entirely when the file is
    untracked or has uncommitted changes (dirty tree: serve stored anchors).
    Legacy rows (anchor_commit NULL) are never touched.
    """
    head: str | None = None
    head_checked = False
    for c in comments:
        anchor = c.get("anchor_commit")
        if not anchor:
            continue
        if not head_checked:
            head = _git_head_if_clean(target)
            head_checked = True
        if head is None or anchor == head:
            continue
        survivors = _blame_surviving_lines(
            target, anchor, head, c["line_start"], c["line_end"]
        )
        if survivors is None:
            continue  # git failure — serve stored anchors unchanged
        if survivors:
            new_start, new_end = min(survivors), max(survivors)
        else:
            # Whole range deleted: clamp to current file length so the
            # comment stays visible on the nearest block.
            new_start = max(1, min(c["line_start"], total_lines))
            new_end = max(1, min(c["line_end"], total_lines))
        update_comment_anchor(
            conn, c["id"],
            line_start=new_start, line_end=new_end, anchor_commit=head,
        )
        c["line_start"], c["line_end"], c["anchor_commit"] = new_start, new_end, head


# ── Block-id anchoring (#465) ───────────────────────────────────────────


def _block_at_line(blocks: list[dict], line: int) -> dict | None:
    """The block whose source range contains *line*, or None.

    A line can fall outside every block: the blank lines between blocks belong
    to no block, and a stale anchor can point past the end of the file.
    """
    for block in blocks:
        if block["start_line"] <= line <= block["end_line"]:
            return block
    return None


def _nearest_block_start(blocks: list[dict], line: int) -> int:
    """Start line of the last block at or above *line* (first block if none).

    Somewhere to put a comment whose anchor no longer lands on a block.  The
    view only renders comments grouped under a real block start, so a group key
    invented from the raw line number would make the comment disappear.
    """
    nearest = blocks[0]["start_line"]
    for block in blocks:
        if block["start_line"] > line:
            break
        nearest = block["start_line"]
    return nearest


def _block_anchor_for_new_comment(
    target: Path, line_start: int
) -> tuple[str | None, int | None, str | None]:
    """Block id, in-block offset and context for a comment on *line_start*.

    Derived server-side from the file as it is on disk, so every client — the
    web form, the JSON API, the CLI — anchors the same way without having to
    know that block ids exist.

    The offset is stored alongside the id because the id alone says *which* text
    a comment is about but not *where in it* the comment sits.  It is an offset
    rather than the block's start line so that it stays true no matter what else
    moves: the blame migration rewrites ``line_start``, and anything measured
    against a stored absolute line would count that displacement a second time.

    It is counted in *normalized* lines, the same space the block id is hashed
    in, so the two anchors cannot disagree: an edit that leaves the id valid
    leaves the offset valid too.

    The context is stored for the case the id alone cannot answer (#467): when
    the document holds several byte-identical blocks, the occurrence number in
    the id names the copy the author picked only until the copies are reordered,
    and the context — the text that came before it — still names it afterwards.
    Recorded at creation for the same reason as the offset: it describes the
    document the author was looking at, which is the only moment it is known.
    """
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, None
    block = _block_at_line(render_markdown_blocks(source), line_start)
    if block is None:
        return None, None, None
    offset = normalized_offset(block["raw"], line_start - block["start_line"])
    return block["block_id"], offset, block["block_context"]


def _fits_sqlite_integer(value: int) -> bool:
    """Whether *value* can be compared against a stored id at all.

    SQLite's INTEGER is signed 64-bit, and binding anything wider raises
    ``OverflowError`` from the driver rather than returning no row, so an id
    this far out has to be ruled out before it reaches a query (#473).
    """
    return -(2**63) <= value < 2**63


def _reject_unusable_comment_id(comment_id: int) -> None:
    """Refuse an id no stored comment could ever have (#475).

    The resolve routes take a client-supplied id straight into an
    ``UPDATE ... WHERE id = ?``.  An id outside the range of a SQLite INTEGER
    does not simply match no row -- the driver raises ``OverflowError`` while
    binding it, so it surfaced as a 500.  That is the caller's mistake reported
    as the server's, which is exactly what #470 and #473 removed for
    ``parent_id``; this is the same rule at the route family that never got it.

    Narrow on purpose, and it stayed narrow after item 2.  This rejects only
    ids that *cannot* name a row; an id inside the range that happens to name
    no comment is the other guard's business (``_reject_unknown_comment``) and
    answers **404**, not 422.  So ``2**63`` is refused here while ``2**63-1``
    is not, even though neither names a comment -- which is exactly what makes
    the bound testable: the two answers differ, so narrowing or widening the
    range by one is visible at this call site.
    """
    if not _fits_sqlite_integer(comment_id):
        raise HTTPException(
            status_code=422,
            detail=f"comment_id {comment_id} cannot name an existing comment",
        )


def _reject_unknown_comment(comment_id: int, row: dict | None) -> None:
    """Refuse an id that names no comment (#475 item 2).

    ``UPDATE comments SET resolved = ... WHERE id = ?`` matches nothing and
    commits happily, so the route redirected 303 -- the same answer a resolve
    that landed gets.  A success reported for something that did not happen is
    not idempotence, and the owner decided (2026-08-17) the caller should be
    told: "No, so caller will know."

    The web UI cannot reach this.  Its Resolve/Unresolve buttons are rendered
    from rows the server just read, so the id always exists.  The CLI and any
    script posting to these routes can, and they are the callers with no human
    watching the page to notice nothing changed.

    404 rather than the 422 its sibling above uses: ``comment_id`` is a path
    segment naming a resource, so "no such comment" is what 404 is for, while
    ``parent_id`` -- which answers 422 for both cases -- is a body field of a
    create request.  Keeping the two apart also keeps the range bound pinned
    (see ``_reject_unusable_comment_id``).

    Takes the row rather than looking it up, so the caller can pass what the
    update already returned instead of paying for a second query.
    """
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"comment {comment_id} does not exist",
        )


def _may_be_content_id_of(file_id: str | None, target: Path) -> bool:
    """Whether *file_id* is *target*'s content identity, or cannot be ruled out.

    Two different answers share the ``True`` here: "it matches the file as it is
    on disk now", and "the file could not be read, so there was nothing to match
    it against".  The name says ``may`` because the caller gets no way to tell
    those apart and must not read the answer as a fact about the document (#503
    item 8).

    Answering needs the file's bytes whether or not the target is tracked, so a
    file the server cannot read has no answer.  An untracked target falls
    straight through to ``content_hash_id()``, which hashes them; a tracked one
    takes the longer route to the same place, because ``git_blob_id()`` shells
    ``git hash-object <path>``, which opens the working file and exits non-zero
    when it cannot -- read as "not tracked", so the fallback hashes the bytes
    after all (#503 item 16).

    ``True`` for an unreadable target, because the caller
    (``_parent_for_new_comment``) uses this to decide whether to *refuse* a
    request: an unreadable file is no evidence that the parent belongs somewhere
    else, and "names a comment on another file" is a claim about the document,
    not about a permission bit.  Letting the ``OSError`` out instead answered a
    500 to a create that base ``c22217e`` accepted (#503 item 3) -- the caller
    cannot act on it, which is what #470/#472 took off this field.

    It is the same answer its neighbours already give for the same file:
    ``_block_anchor_for_new_comment()`` and ``_rendered_block_of()`` both fall
    back to "no placement to compare against" rather than to a refusal.  Kept
    out of ``derive_file_id()`` on purpose: an identity function that invents an
    id for a file it cannot read would hand a wrong one to the callers that
    *store* and *query* by it.

    Failing open is a trade, and this is what it costs (#503 item 6).  A legacy
    parent that really does belong to another file gets through the guard while
    the target is unreadable.  Measured: a ``file_path IS NULL`` row whose
    ``file_id`` names ``twin.md``, with ``doc.md`` chmod ``0o000`` across the
    create -- the create answers 201, the reply is stored under that parent,
    and once the file is readable again the reply renders at ``depth 0`` in a
    view that does not contain its parent at all.  That is the "201 that is not
    a reply" the rules in ``_parent_for_new_comment`` exist to refuse, reached
    through this door; and the permission bit that opens it is transient while
    the row it admits is not.  Still the better trade than a 500 the caller can
    do nothing with -- and what base ``c22217e`` did anyway -- but a cost, not
    the absence of one.
    """
    try:
        return file_id == derive_file_id(str(target))
    except OSError:
        return True


def _parent_for_new_comment(
    conn, *, explicit: int | None, block_id: str | None, path: str, target: Path
) -> int | None:
    """The comment a new comment on this block replies to (#465 Phase 2).

    The rule this implements, from #465: "when there is already a comment on a
    certain block, all new comments on that same block should be treated as
    reply to the previous reply or the first comment of that block."  So the
    parent is the *latest* comment on the block — the first comment while it is
    the only one, each later reply after that — which chains a block's
    conversation in the order it was written instead of leaving a pile of
    unrelated roots.

    Derived server-side, so every client threads identically: the web form used
    to hardcode ``parent_id=0`` and could never produce a reply, and the CLI
    posts over the same HTTP routes.

    Derivation fills the gap left by a caller that names no parent; it does not
    overrule one that does.  Answering a specific earlier comment is exactly
    what ``parent_id`` is for — but "explicitly" is not "unconditionally", and
    the id still has to name a comment this reply can hang under (below).  A
    comment on no block at all (the blank line between two blocks, or a file
    that cannot be read) stays a root: it has no block identity to thread on,
    and guessing one from the line number is the mistake Phase 1 exists to
    avoid.

    An explicit id is honoured only once it names a real comment (#470).
    ``parent_id`` is a foreign key under ``PRAGMA foreign_keys=ON``, so an id
    that resolves to no row used to reach the insert and raise an unhandled
    ``IntegrityError`` — a 500, which tells the caller nothing: naming a
    comment that is not there is the caller's mistake, not the server's.

    That rule holds for *every* id no comment can have, not only the plausible
    ones (#473).  ``0`` and no id at all still mean "no parent" and fall
    through to derivation — the old web form posted a literal ``0`` and legacy
    callers still do.  Anything else is a request to reply to one specific
    comment, so a negative id is rejected like an unknown one instead of
    slipping into derivation and answering with a parent the caller never
    named, and an id outside the range of a SQLite INTEGER is rejected rather
    than overflowing inside the lookup and surfacing as the same 500 #470 set
    out to remove.  The range is checked first because the lookup is what
    overflows.

    Naming a *real* comment is still not enough: it has to be a comment on this
    block of this file (#473 item 3).  A thread is rendered per block, and #469
    established that a root outside the block being rendered cannot be reached
    from it — so a reply stored under a parent in another block, or another
    file, comes back as its own root, and the 201 reported an answer that is
    not attached to the comment it answers.  Refusing it says so instead.

    Some things that look foreign are not.  "Another file" is decided the way
    ``latest_comment_in_block()`` already scopes a block's thread — by
    ``file_path`` when the row carries one, by ``file_id`` when it predates the
    column — so a legacy row is not read as foreign for want of a column added
    later, nor for a target whose id cannot be derived at all (see
    ``_may_be_content_id_of``, which fails open and says what that costs).  An
    absent ``block_id`` on either side is not a *different* block either:
    pre-#465 rows and comments between blocks have none, and a row with none is
    placed by its line, which is often this very block.

    And "another block" has to mean the block the parent is *displayed* on, not
    the id it was stored with.  The read path re-places a row whose block is
    gone from the document (#468), so once somebody edits the paragraph a
    comment sits on, its stored id names no block while the comment still
    renders — under the block at its line, flagged ``detached``.  Comparing
    stored ids there would refuse a create that produces a properly nested
    reply, and say "another block" about the block the reader can see the
    parent on.  So the parent is put through ``_rendered_block_of()``, which
    answers the whole of that question — matcher first, then the same fall back
    to the line the view uses — because the half-answer cuts both ways: a
    detached parent whose fallback group *is* this block would be refused for a
    block neither comment is on, and one whose fallback group is another block
    would be accepted although the reply comes back a root at ``depth 0``.

    (Only a parent carrying a stored ``block_id`` is resolved at all.  That is
    the rule above — a row with none is in no block rather than another one —
    kept here as a fast path as well, since it saves reading and rendering the
    file on every explicit-parent create; ``_rendered_block_of()`` answers
    ``None`` for such a row anyway, so the two cannot disagree.)
    """
    if explicit:
        parent = get_comment(conn, explicit) if _fits_sqlite_integer(explicit) else None
        if parent is None:
            raise HTTPException(
                status_code=422,
                detail=f"parent_id {explicit} does not name an existing comment",
            )
        if parent["file_path"] != path and not (
            parent["file_path"] is None
            and _may_be_content_id_of(parent["file_id"], target)
        ):
            raise HTTPException(
                status_code=422,
                detail=f"parent_id {explicit} names a comment on another file",
            )
        if block_id and parent["block_id"]:
            rendered_on = _rendered_block_of(parent, target)
            if rendered_on is not None and rendered_on["block_id"] != block_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"parent_id {explicit} names a comment on another block",
                )
        return explicit
    if not block_id:
        return None
    latest = latest_comment_in_block(
        conn,
        block_id=block_id,
        file_path=path,
        file_id=derive_file_id(str(target)),
    )
    return latest["id"] if latest else None


def _thread_root_of(
    comments: list[dict], group_of: dict[int, int]
) -> dict[int, dict]:
    """Map every comment to the root of its thread *within its own block group*.

    Walks ``parent_id`` up and stops at a comment with no parent, one that is
    not among *comments* — a parent on another file, or one never fetched — or
    one that is grouped under a different block.

    That last stop is what keeps the answer usable.  A thread is rendered per
    block, so a root in another group is a root the reader cannot see: treating
    it as this comment's root would indent the reply under nothing and sort it
    by a timestamp from a thread that is not on screen, which can put it above
    the root of the block it is actually in.  Parent and child normally share a
    block, and so share a group; they come apart when a comment detaches, and
    then the reply has to stand on its own.

    Every comment resolves to a member of *comments* in its own group (its own
    row at worst), so the caller can order by thread without any row falling
    outside the ordering.  The visited set is what makes that true even of a
    parent cycle, which the routes cannot create but a hand-edited DB could —
    without it the walk never terminates and the view never answers.
    """
    by_id = {c["id"]: c for c in comments}
    root_of: dict[int, dict] = {}
    for comment in comments:
        current = comment
        seen = {comment["id"]}
        while True:
            parent = by_id.get(current["parent_id"]) if current["parent_id"] else None
            if parent is None or parent["id"] in seen:
                break
            if group_of[parent["id"]] != group_of[comment["id"]]:
                break
            seen.add(parent["id"])
            current = parent
        root_of[comment["id"]] = current
    return root_of


def _matching_block(
    c: dict, by_id: dict[str, dict], by_digest: dict[str, list[dict]]
) -> dict | None:
    """The block in the current render that a comment's stored id names (#467).

    The id is ``digest-occurrence``.  The digest is the block's *text*: a block
    with that digest holds exactly the text the comment was written about, so
    matching on the digest can never place a comment on other text.  The
    occurrence number is not identity in the same sense — it says only how many
    identical blocks came before this one when the comment was written, which
    changes when a copy is inserted above or an earlier copy is deleted.  So the
    digest decides, and the occurrence number is demoted to a last-resort
    tiebreak between blocks that are already byte-identical.

    - one block carries the digest: it is the answer, whatever its occurrence
      number now is.  This is the delete-an-earlier-copy case, where the
      comment's own text is plainly still in the file and only the numbering
      moved.
    - several carry it, and the comment was written with a context: the copy
      whose context still matches is the copy its author chose.
    - several carry it and the context matches none or more than one of them
      (an adjacent run shares a context, and a legacy row has none at all):
      fall back to the stored occurrence number, exactly as before #467.

    Returning ``None`` means the text is gone from the document, which is what
    ``detached`` reports.  A fallback that answered "some block with different
    text" instead is the failure this whole scheme exists to prevent.
    """
    stored = c.get("block_id")
    if not stored:
        return None
    candidates = by_digest.get(stored.rsplit("-", 1)[0], [])
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        context = c.get("block_context")
        if context is not None:
            matches = [b for b in candidates if b["block_context"] == context]
            if len(matches) == 1:
                return matches[0]
    return by_id.get(stored)


def _rendered_block_of(comment: dict, target: Path) -> dict | None:
    """The block *comment* is displayed on in *target* as it is on disk now.

    The answer ``_resolve_comment_blocks()`` reaches for the view, for a single
    row and without the migration or the backfill it does on the way: what
    ``_matching_block()`` makes of the stored id, and — when that matches
    nothing — the same fall back to the line that puts a *detached* comment
    somewhere the reader can still see it.

    Both steps are needed, because both are what the reader is shown.  A
    detached row is not an unplaced one: ``_matching_block()`` returning
    ``None`` says the text is gone, and the view answers that by grouping the
    comment under the block at its line — or, for a line inside no block, the
    nearest one above — and flagging it ``detached`` (#468/#496/#497).
    Stopping at the matcher and calling ``None`` "no opinion" would let a reply
    hang off a parent the reader sees in another group, at ``depth 0``, which
    is the 201-that-is-not-a-reply the guard exists to refuse.

    Asking it this way is what keeps the create-time parent guard and the read
    path from disagreeing about which block a comment is on (#473 item 3); a
    guard reading the stored id directly rejects replies to any comment whose
    paragraph has since been edited.

    Two things it deliberately does not answer.  A row carrying no ``block_id``
    gets ``None`` rather than the block at its line: the read path *invents* an
    identity for such a row and backfills it, and a create-time guard that
    refused on an invented one would be guessing block identity from a line
    number, which is the mistake Phase 1 exists to avoid.  And the placement is
    computed from the line as stored: the blame migration that could move it
    first (#406) shells out to git and persists, which is not a thing to do
    inside a create guard — it has already run on every read of this file, so
    the stored line is the one the last view placed by.

    ``None`` for an unreadable file or one that renders to no blocks is the same
    answer for the same reason: there is no placement to compare against.  Both
    are reachable only if the file changes between deriving the new comment's
    own anchor and this call — with the file unreadable or blockless at anchor
    time the reply has no ``block_id`` of its own and the caller never asks.
    """
    if not comment.get("block_id"):
        return None
    try:
        source = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    blocks = render_markdown_blocks(source)
    if not blocks:
        return None
    by_digest: dict[str, list[dict]] = {}
    for b in blocks:
        by_digest.setdefault(b["block_id"].rsplit("-", 1)[0], []).append(b)
    matched = _matching_block(comment, {b["block_id"]: b for b in blocks}, by_digest)
    if matched is not None:
        return matched
    # Detached: placed exactly as the view places it, through the view's own
    # helpers, so the two cannot drift apart into disagreeing about the group a
    # reader is looking at.
    fallback = _block_at_line(blocks, comment["line_start"])
    if fallback is not None:
        return fallback
    by_start = {b["start_line"]: b for b in blocks}
    return by_start[_nearest_block_start(blocks, comment["line_start"])]


def _resolve_comment_blocks(
    conn, comments: list[dict], blocks: list[dict], target: Path, total_lines: int
) -> dict[int, int]:
    """Anchor each comment to a block, and say which one (by start line).

    Resolution order (#465):

    1. ``block_id`` match against the current render (see ``_matching_block()``
       for how one of several identical blocks is chosen) — survives the block
       being moved, and works on dirty trees and non-git roots, where the blame
       migration cannot run at all;
    2. else the ``anchor_commit`` reverse-blame migration (#406);
    3. else the stored line numbers.

    The blame migration runs first because it is the one step that *persists*:
    it owns the stored anchor, as it has since #406.  A block-id match then
    overrides where the comment is shown, placing it at the block's current
    start plus its in-block offset — display only, so a dirty tree still serves
    the stored anchors it did before.

    **When the two disagree, the stored line wins.**  Blame is evidence: it
    followed the real text through a real commit.  The offset is an estimate,
    and an estimate that resolves to no line of the block it names has been
    invalidated by something — so it is discarded rather than clamped into
    range, and the comment keeps the line blame gave it.  Overruling correct
    evidence with a guess and reporting the result as attached is the worst of
    both; ``detached`` is what says an anchor was genuinely lost.

    Comments are never dropped: one that matches nothing still comes back,
    grouped under the nearest block and flagged ``detached`` so the reader can
    see it has lost its text instead of trusting it against unrelated content.
    A row that had no ``block_id`` and resolved by line gets one written back,
    so the DB self-heals as files are viewed — but only when the served file is
    not a dirty tracked file.  There the blame migration has not run, so the
    stored line may already have drifted, and a block id written from it would
    outrank blame forever and cement the drift.
    """
    _migrate_comment_anchors(conn, comments, target, total_lines)

    by_id = {b["block_id"]: b for b in blocks}
    by_digest: dict[str, list[dict]] = {}
    for b in blocks:
        by_digest.setdefault(b["block_id"].rsplit("-", 1)[0], []).append(b)
    group_of: dict[int, int] = {}
    unmatched: list[dict] = []
    # Shells out to git, so it is answered at most once per view and only if a
    # row actually needs backfilling — in the steady state, where every comment
    # already carries an id, the question is never asked.
    backfill_blocked: bool | None = None

    for c in comments:
        block = _matching_block(c, by_id, by_digest)
        if block is None:
            unmatched.append(c)
            continue
        # Move the reported range with the block, keeping its length: a range
        # may span several blocks, and truncating it to the anchor block would
        # silently shrink what the comment claims to be about.
        #
        # The comment is placed from the block's *current* start plus the offset
        # it was written at, rather than by shifting its stored line.  The stored
        # line is not a fixed reference: `_migrate_comment_anchors()` above has
        # already moved it for a committed edit, so shifting it again would count
        # that displacement twice.  Rows written before the offset existed have
        # none to place by, and rows whose offset names no line of this block no
        # longer describe a position in it; both keep their migrated line as-is.
        offset = c.get("block_offset")
        within = raw_offset(block["raw"], offset) if offset is not None else None
        if within is not None:
            # `within` indexes a line of this block, so the start is inside it
            # and inside the file by construction — no clamping needed.  The end
            # still needs one: a range may span several blocks, and it keeps its
            # length rather than being truncated to the anchor block, because
            # shrinking it would misstate what the comment is about.
            span = max(c["line_end"] - c["line_start"], 0)
            start = block["start_line"] + within
            c["line_start"] = start
            c["line_end"] = min(start + span, max(total_lines, start))
        c["detached"] = False
        group_of[c["id"]] = block["start_line"]

    for c in unmatched:
        block = _block_at_line(blocks, c["line_start"])
        if block is None:
            # No block at this line: past the end of the file, or between
            # blocks.  Keep it visible, but say that it is adrift.
            c["detached"] = True
            group_of[c["id"]] = _nearest_block_start(blocks, c["line_start"])
        else:
            # A stored block_id that matched nothing means the block it was
            # written about is gone; the line-based hit is a coincidence, not
            # the same text.
            c["detached"] = bool(c.get("block_id"))
            group_of[c["id"]] = block["start_line"]
            if not c.get("block_id"):
                if backfill_blocked is None:
                    backfill_blocked = _is_tracked_and_dirty(target)
                if not backfill_blocked:
                    offset = normalized_offset(
                        block["raw"], c["line_start"] - block["start_line"]
                    )
                    set_comment_block_id(
                        conn,
                        c["id"],
                        block["block_id"],
                        offset,
                        block_context=block["block_context"],
                    )
                    c["block_id"] = block["block_id"]
                    c["block_offset"] = offset
                    c["block_context"] = block["block_context"]
        # Nothing on this path moves a stale line, so it can still name a line
        # the file has since lost — `L7` of a 3-line file, which the reader
        # cannot scroll to (#468).  Clamped last, so the group and the backfill
        # offset are still derived from the line as stored: the clamp governs
        # what is reported, not where the comment is thought to belong.
        c["line_start"] = max(1, min(c["line_start"], total_lines))
        c["line_end"] = max(c["line_start"], min(c["line_end"], total_lines))

    return group_of


def _placed_comments(
    conn, path: str, file_path: Path, blocks: list[dict], total_lines: int
) -> tuple[list[dict], dict[int, int]]:
    """Load *path*'s comments and place them against the current render.

    The single entry point for **every** read surface (#468).  Before this,
    ``_resolve_comment_blocks()`` was reachable only through
    ``build_view_payload()``, so the web view followed a comment to the block
    it was written about while ``GET /api/comments`` — and therefore
    ``comments_cli.py``, and therefore every agent reading through it —
    returned the raw stored line and no ``detached`` flag.  On a dirty tree
    those are different lines holding different text for the same comment id,
    and the surface that got it wrong is the one most likely to be acted on.

    Placement is written onto the comment dicts in place rather than offered
    alongside the stored values: a caller that has to opt in is a caller that
    can forget to, which is the whole failure being fixed here.  ``line_start``
    means "where this comment is now" on both surfaces or on neither.

    Returns the comments (unordered — each surface sorts for its own reader)
    and each one's containing block start line, which only the view uses.
    """
    # Look up comments by file_path — the stable identity across edits (#404).
    # Merge in legacy rows (file_path IS NULL) that match the current content
    # id, deduped by comment id.
    comments = list_comments_by_path(conn, path)
    seen_ids = {c["id"] for c in comments}
    legacy = [
        c
        for c in list_comments(conn, derive_file_id(str(file_path)))
        if c["file_path"] is None and c["id"] not in seen_ids
    ]
    comments = comments + legacy

    # Anchor every comment to a block: by block_id (#465), else by the
    # anchor_commit blame migration (#406), else by its stored lines.
    group_of = _resolve_comment_blocks(conn, comments, blocks, file_path, total_lines)
    return comments, group_of


# ── Routes ──────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Directory browser / file list."""
    files = _list_files(_source_root)
    resp = templates.TemplateResponse(
        request,
        "index.html",
        context={"files": files, "root": str(_source_root)},
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


# Keys of build_view_payload() that /api/source exposes; `blocks` is omitted
# because the client renders them through its own warm runtime (#447).
API_SOURCE_KEYS = ("path", "file_id", "source", "toc", "comments_by_block")


def build_view_payload(path: str) -> dict:
    """Build everything needed to present *path*: blocks, TOC, comments.

    Single source of truth shared by ``/view`` (server-side render) and
    ``/api/source`` (SPA soft navigation, #447) so the two cannot drift.
    """
    file_path = _resolve_file(path)
    source = file_path.read_text(encoding="utf-8", errors="replace")
    blocks = render_markdown_blocks(source)
    toc = extract_toc(source)
    fid = derive_file_id(str(file_path))

    conn = _conn()
    try:
        comments, group_of = _placed_comments(
            conn, path, file_path, blocks, len(source.splitlines())
        )

        # Order each block's comments as threads (#465 Phase 2): a root,
        # then the replies chained onto it, then the next root.  A reply is
        # written after the root it answers but not necessarily after every
        # other root, so time order alone would separate a reply from what it
        # replies to.
        root_of = _thread_root_of(comments, group_of)
        for c in comments:
            # Replies nest under their *root*, not one indent deeper per hop:
            # the chain is how a thread is recorded, two levels is how it is
            # read.  A reply whose root is not in the same block group resolves
            # to itself and so renders as a root — visible, and not indented
            # under a comment the reader cannot see.
            c["depth"] = 0 if root_of[c["id"]] is c else 1

        comments = sorted(
            comments,
            key=lambda c: (
                group_of[c["id"]],
                root_of[c["id"]]["created_at"],
                root_of[c["id"]]["id"],
                c["depth"],
                c["created_at"],
                c["id"],
            ),
        )

        # Group ALL comments by their containing block's start_line.
        # One list per block, as #402 left it: no reply_map, no nested markup.
        # The nesting Phase 2 adds is carried by `depth` and by the order, so
        # every comment — root, reply, or reply whose root is elsewhere —
        # still appears in exactly one block's list.
        comments_by_block: dict[int, list[dict]] = {}
        for c in comments:
            comments_by_block.setdefault(group_of[c["id"]], []).append(c)
    finally:
        conn.close()

    return {
        "path": path,
        "file_id": fid,
        "source": source,
        "toc": toc,
        "comments_by_block": comments_by_block,
        "blocks": blocks,
    }


@app.get("/view", response_class=HTMLResponse)
async def view_file(request: Request, path: str = Query(...)):
    """Render a file with per-line anchors and comments."""
    payload = build_view_payload(path)
    # Row/TOC/header markup comes from the same Python builders the client
    # runs after a soft swap (#451), so the two renders cannot drift.
    resp = templates.TemplateResponse(
        request,
        "view.html",
        context={
            **payload,
            "files": _list_files(_source_root),
            "row_specs": source_row_specs(
                payload["blocks"], payload["comments_by_block"]
            ),
            "toc_specs": toc_item_specs(payload["toc"]),
            "header": header_fields(payload),
        },
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/source")
async def api_source(path: str = Query(...)):
    """Return the raw source + TOC + comments for a file as JSON (#447).

    Used by the client to switch files without a full page load (which would
    re-boot the Pyodide runtime).  ``blocks`` are deliberately omitted: the
    client renders them through its already-warm runtime.
    """
    payload = build_view_payload(path)
    resp = JSONResponse(
        content={k: payload[k] for k in API_SOURCE_KEYS}
    )
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/comment")
async def add_comment(
    file_id: str = Form(...),
    path: str = Form(...),
    line_start: int = Form(...),
    line_end: int = Form(...),
    author: str = Form("anon"),
    body: str = Form(...),
    parent_id: int | None = Form(None),
):
    """Create a comment (or reply) and redirect back to the file view."""
    target = _resolve_file(path)  # validate path is inside source root
    block_id, block_offset, block_context = _block_anchor_for_new_comment(
        target, line_start
    )
    conn = _conn()
    try:
        create_comment(
            conn,
            file_id=file_id,
            line_start=line_start,
            line_end=line_end,
            author=author,
            body=body,
            parent_id=_parent_for_new_comment(
                conn, explicit=parent_id, block_id=block_id, path=path, target=target
            ),
            file_path=path,
            anchor_commit=_git_head_if_clean(target),
            block_id=block_id,
            block_offset=block_offset,
            block_context=block_context,
        )
    finally:
        conn.close()
    return RedirectResponse(
        url=f"/view?path={quote(path)}#L{line_start}",
        status_code=303,
    )


@app.post("/comment/{comment_id}/resolve")
async def resolve(comment_id: int, path: str = Form(...)):
    _resolve_file(path)
    _reject_unusable_comment_id(comment_id)
    conn = _conn()
    try:
        _reject_unknown_comment(comment_id, resolve_comment(conn, comment_id))
    finally:
        conn.close()
    return RedirectResponse(url=f"/view?path={quote(path)}", status_code=303)


@app.post("/comment/{comment_id}/unresolve")
async def unresolve(comment_id: int, path: str = Form(...)):
    _resolve_file(path)
    _reject_unusable_comment_id(comment_id)
    conn = _conn()
    try:
        _reject_unknown_comment(comment_id, unresolve_comment(conn, comment_id))
    finally:
        conn.close()
    return RedirectResponse(url=f"/view?path={quote(path)}", status_code=303)


# ── JSON API (#435) ───────────────────────────────────────────────────


@app.get("/api/comments")
async def api_get_comments(path: str = Query(...)):
    """Return JSON list of comments for a file.

    ``line_start``/``line_end`` are where the comment sits in the file *as it
    is on disk now*, and ``detached`` says when that could not be worked out —
    the same placement ``/view`` shows, through the same code (#468).
    """
    # Validates the path is inside the source root, returns 403/404.
    file_path = _resolve_file(path)
    source = file_path.read_text(encoding="utf-8", errors="replace")
    conn = _conn()
    try:
        comments, _ = _placed_comments(
            conn,
            path,
            file_path,
            render_markdown_blocks(source),
            len(source.splitlines()),
        )
        # Sorted by the *placed* line, so the listing reads in the order the
        # reader will meet the comments in the file.
        comments.sort(key=lambda c: (c["line_start"], c["created_at"]))
    finally:
        conn.close()
    return JSONResponse(content=comments)


class _CommentCreate(BaseModel):
    file_id: str
    path: str
    line_start: int
    line_end: int
    author: str = "anon"
    body: str
    parent_id: int | None = None


@app.post("/api/comments", status_code=201)
async def api_post_comment(payload: _CommentCreate):
    """Create a comment from a JSON body, return the created comment as JSON."""
    target = _resolve_file(payload.path)
    block_id, block_offset, block_context = _block_anchor_for_new_comment(
        target, payload.line_start
    )
    conn = _conn()
    try:
        comment = create_comment(
            conn,
            file_id=payload.file_id,
            line_start=payload.line_start,
            line_end=payload.line_end,
            author=payload.author,
            body=payload.body,
            parent_id=_parent_for_new_comment(
                conn,
                explicit=payload.parent_id,
                block_id=block_id,
                path=payload.path,
                target=target,
            ),
            file_path=payload.path,
            anchor_commit=_git_head_if_clean(target),
            block_id=block_id,
            block_offset=block_offset,
            block_context=block_context,
        )
    finally:
        conn.close()
    return JSONResponse(content=comment, status_code=201)


# ── Blame JSON API (#443 spike) ──────────────────────────────────────────

@app.get("/api/blame")
async def api_blame(path: str = Query(...)):
    """Return per-line git-blame data as JSON for a tracked file.

    Response: ``{"lines": [{"line": int, "commit": str, "author": str,
    "date": str, "content": str}, ...]}``
    """
    target = _resolve_file(path)
    d = str(target.parent)
    name = target.name
    try:
        result = subprocess.run(
            ["git", "-C", d, "blame", "--porcelain", "--", name],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise HTTPException(status_code=502, detail="git blame unavailable")
    if result.returncode != 0:
        raise HTTPException(
            status_code=404,
            detail="File is not git-tracked or blame failed",
        )

    # Parse porcelain output into per-line records.
    lines: list[dict] = []
    current_commit = ""
    commit_meta: dict[str, dict] = {}  # commit -> {author, date}
    line_num = 0
    for raw_line in result.stdout.splitlines():
        m = _BLAME_HEADER_RE.match(raw_line)
        if m:
            current_commit = m.group(1)
            line_num = int(m.group(3))
            if current_commit not in commit_meta:
                commit_meta[current_commit] = {"author": "", "date": ""}
        elif raw_line.startswith("author "):
            commit_meta[current_commit]["author"] = raw_line[7:]
        elif raw_line.startswith("author-time "):
            commit_meta[current_commit]["date"] = raw_line[12:]
        elif raw_line.startswith("\t"):
            meta = commit_meta.get(current_commit, {"author": "", "date": ""})
            lines.append({
                "line": line_num,
                "commit": current_commit[:12],
                "author": meta["author"],
                "date": meta["date"],
                "content": raw_line[1:],
            })

    return JSONResponse(content={"lines": lines})


# ── Render / parity-fixture API (#443 spike) ─────────────────────────────


@app.post("/api/render")
async def api_render(request: Request):
    """Render markdown source and return block ranges as JSON.

    Request body: ``{"source": "<markdown text>"}``
    Response: ``{"blocks": [{"start_line": int, "end_line": int}, ...]}``
    """
    body = await request.json()
    source = body.get("source", "")
    blocks = render_markdown_blocks(source)
    return JSONResponse(content={
        "blocks": [
            {"start_line": b["start_line"], "end_line": b["end_line"]}
            for b in blocks
        ]
    })


@app.get("/api/parity-fixture")
async def api_parity_fixture():
    """Return the canonical parity-test fixture and its expected ranges.

    Used by the Pyodide preview page to run the client-side parity check
    against the same fixture used by test_parity.py.
    """
    from parity_fixture import EXPECTED_RANGES, PARITY_FIXTURE

    return JSONResponse(content={
        "source": PARITY_FIXTURE,
        "expected_ranges": [
            {"start_line": s, "end_line": e} for s, e in EXPECTED_RANGES
        ],
    })


# ── Pyodide spike preview (#443) ─────────────────────────────────────────


@app.get("/spike/preview", response_class=HTMLResponse)
async def spike_preview(request: Request):
    """Pyodide-powered in-browser markdown preview (spike page)."""
    return templates.TemplateResponse(
        request,
        "spike_preview.html",
        context={},
    )


@app.get("/spike/renderer.py")
async def spike_renderer_source():
    """Serve renderer.py source for Pyodide to load verbatim."""
    source = (BASE_DIR / "renderer.py").read_text(encoding="utf-8")
    return JSONResponse(content={"source": source})


# ── Shared Python modules served to the browser (#451) ──────────────────


@app.get("/py/view_specs.py")
async def view_specs_source():
    """Serve view_specs.py source for Pyodide to load verbatim (#451).

    The client executes the very module the server imports, so the soft-swap
    markup and the Jinja markup are built by the same code.  ``no-store``
    keeps that guarantee airtight: a cached copy could run stale builder code
    against freshly rendered server markup.
    """
    source = (BASE_DIR / "view_specs.py").read_text(encoding="utf-8")
    resp = JSONResponse(content={"source": source})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ── Entrypoint ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="doc-review server")
    parser.add_argument("source", help="File or directory to serve for review")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28080)
    parser.add_argument("--db", default="comments.db", help="SQLite DB path")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    if not source.exists():
        parser.error(f"Source path does not exist: {source}")

    # If a single file is given, serve its parent directory.
    if source.is_file():
        configure(source.parent, args.db)
    else:
        configure(source, args.db)

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
