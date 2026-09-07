# doc-review

Block-anchored markdown review web app. Point it at a directory of markdown, read the files in a
browser, and attach comments that stay anchored to a block of text as the document is edited around
them.

It does three things:

- **Render and review.** Browse a source tree, read rendered markdown, and thread comments against
  individual blocks.
- **Serve a JSON API.** Read and post comments over HTTP, so an agent or a script can review a
  document without a browser.
- **Present a deck.** A document whose front matter declares `marp: true` can be shown as slides,
  over the same blocks review mode renders.

## Quick Reference

```bash
# Install dependencies (Python 3.10+), declared in pyproject.toml.
# Every command in this block is run from the monorepo root.
pip install -e doc-review

# Add the test dependencies as well
pip install -e "doc-review[dev]"

# Run server (serve a directory for review)
python doc-review/server.py /path/to/docs --host 127.0.0.1 --port 28080

# Run tests
cd doc-review && pytest -v
```

`server.py` takes the directory to serve as its one positional argument, plus `--host`, `--port` and
`--db`. The database defaults to `comments.db` in the working directory. Nothing outside the served
directory is reachable: a path that climbs out of it is refused with a 403.

## Render and review

`GET /` lists the markdown files under the served root. `GET /view?path=<rel>` renders one of them.

The view is three columns on a desktop and stacked panels on a phone:

- A **file navigator** with a filter box and a collapsible tree.
- The **document**, one table row per markdown block, with the block's line range in the gutter.
  A block is a heading, paragraph, list, table, code fence or similar. Clicking one opens the
  comment form for it.
- A **comment sidebar** showing the comments placed on the current file.

Relative links between served files are rewritten to `/view` URLs, so a link to a sibling document
navigates inside the app rather than 404ing.

Comments thread. Posting against a block that already carries a comment appends to that block's
thread instead of starting a second one. Each comment can be resolved and unresolved, and
`POST /comment/{id}/resolve` and `.../unresolve` are the routes behind those controls.

The interesting part is where a comment goes when the document changes. Every block carries a
`block_id` derived from its own normalized source plus an occurrence index, and that id is stored
with the comment. On every read, a comment is placed by three fallbacks in order:

1. Its `block_id`, matched against the blocks of the file as it is on disk now.
2. The `anchor_commit` reverse-blame migration, which walks git history to find where the lines went.
3. Its stored line numbers.

A comment whose block no longer exists is still shown, flagged *detached*. It is never silently
reattached to unrelated text. The web view and `GET /api/comments` run this through the same code,
so both place a comment on the same line.

Files are identified by git blob object id when they are tracked, and by a path plus content
SHA-256 otherwise. Comments therefore survive a rename of the file as well as an edit inside it.

## API access

The JSON API is the headless surface. It speaks the same data the web view does, and it needs no
JavaScript.

| Route | Purpose |
| --- | --- |
| `GET /api/comments?path=<rel>` | Comments for a file, placed against the current text and sorted by line. |
| `POST /api/comments` | Create a comment or reply. Returns the created comment with `201`. |
| `GET /api/source?path=<rel>` | Raw source, TOC, file id and comments for a file, without rendered blocks. |
| `GET /api/blame?path=<rel>` | Per-line git blame for a tracked file. `404` when untracked, `502` when git does not answer. |
| `POST /api/render` | Parse `{"source": "..."}` and return each block's `start_line` and `end_line`. |
| `GET /api/parity-fixture` | The canonical fixture and expected block ranges, used to check the client renderer against the server one. |

Reading comments:

```bash
curl 'http://127.0.0.1:28080/api/comments?path=notes/design.md'
```

Each entry carries `id`, `file_id`, `file_path`, `line_start`, `line_end`, `author`, `body`,
`parent_id`, `resolved`, `created_at`, `updated_at`, `detached`, and the anchoring fields
`block_id`, `block_offset`, `block_context` and `anchor_commit`. The line numbers are where the
comment sits in the file as it is on disk now, not where it was written. `detached` says the
placement could not be worked out.

Posting a comment:

```bash
curl -X POST http://127.0.0.1:28080/api/comments \
  -H 'Content-Type: application/json' \
  -d '{"file_id": "<id>", "path": "notes/design.md",
       "line_start": 12, "line_end": 12,
       "author": "overlord", "body": "This claim needs a citation."}'
```

`file_id` comes from `GET /api/source`. Pass `parent_id` to reply to a specific comment; leave it
out and the server threads the new comment onto whatever its block already carries.

`comments_cli.py` wraps the two comment routes so a script does not have to build the JSON itself:

```bash
python comments_cli.py list --path notes/design.md [--json]
python comments_cli.py post --path notes/design.md --file-id <id> \
    --line-start 12 --line-end 12 --body "This claim needs a citation."
```

It talks to the HTTP API rather than the database, defaults to `http://127.0.0.1:28080`, and signs
posts as `overlord` unless `--author` says otherwise.

There is no authentication. Every route is open to anything that can reach the port.

## Presentation support

A markdown file can be presented as a slide deck. The syntax is a subset of
[Marp](https://marp.app/), and the deck is a grouping of the same blocks review mode renders, so a
comment keeps its anchor across a mode flip.

Presentation mode is read-only. The comment UI is unmounted while a deck is on screen.

### Turning it on

The document must declare itself a deck in front matter, in a `---` block that opens on line 1:

```markdown
---
marp: true
theme: gaia
paginate: true
---

# First slide
```

Declaring `marp: true` is what makes the **Present** button appear. Without it the button stays
hidden, because two `---` rules in ordinary prose are not a deck.

Front-matter directives:

| Directive | Values | Effect |
| --- | --- | --- |
| `marp` | `true` | Required. Offers the document for presentation. |
| `theme` | `default`, `gaia`, `uncover` | Deck styling. An unrecognised name falls back to `default`. |
| `paginate` | `true` | Shows a slide number on each slide. |

### Slide breaks

A slide is the run of blocks between two `---` breaks:

```markdown
---
marp: true
---

# Title slide

Some opening text.

---

## Second slide

- a point
- another point
```

Only `---` cuts a slide. `***` and `___` are thematic breaks in markdown too, but they stay visible
as rules on the slide rather than splitting the deck. A `---` inside a code fence is content, and a
`---` directly under a line of text is a setext heading underline. Neither one splits anything.

Adjacent breaks do not produce a blank slide.

### Per-slide layouts

A layout is chosen with an HTML comment carrying a `class` directive, in either of Marp's two
spellings:

- `<!-- _class: title -->` is a spot directive. It applies to the slide it sits on and no other.
- `<!-- class: title -->` is global. It applies from that slide onward until another `class:`
  replaces it.

```markdown
---
marp: true
---

<!-- _class: title -->

# The Title

An opening line.

---

<!-- _class: quote -->

> Only the slide carrying the directive is styled as a quote.

---

## Back to the default layout
```

Available layouts are `default`, `title`, `centered` and `quote`. An unrecognised name falls back to
`default`.

A comment block is treated as a directive only when it is entirely one HTML comment, every line
inside it reads `key: value`, and at least one key is `class` or `_class`. An ordinary
`<!-- TODO: later -->` is left alone and stays a commentable block. A directive shown as an example
inside indented or fenced code is content, not a directive.

### Presenting

The **Present** button opens the deck full screen where the browser allows it, and falls back to a
fixed overlay otherwise. Arrow keys or the on-screen arrows move between slides. `Esc` or the exit
control returns to review mode.

## Architecture

- **Server:** FastAPI, server-rendered HTML (Jinja2), minimal vanilla JS
- **Storage:** SQLite (`comments.db`) for block-anchored comments
- **File identity:** git blob object id for git-tracked files; path+content SHA-256 fallback
- **Comment anchoring:** `block_id`, then `anchor_commit` reverse blame, then stored line numbers,
  as described under [Render and review](#render-and-review)
- **Rendering:** markdown source parsed by markdown-it-py into blocks, rendered to HTML with
  per-block anchors and a TOC
- **Client renderer:** the browser re-renders blocks through a Pyodide-hosted copy of the same
  Python renderer, so a file can be swapped without a page load. `view_specs.py` holds the row, TOC
  and header specs both paths share, and `/api/parity-fixture` is how the two are held to the same
  output
- **Responsive:** desktop = sidebar; mobile = inline expandable panels

The `id="L{start_line}"` anchor is what comments attach to. It must not change; see the 2026-07-22
comment-loss incident.

## Project Structure

```
doc-review/
├── server.py          # FastAPI app + CLI entrypoint
├── db.py              # SQLite data layer (comments CRUD)
├── file_id.py         # File identity derivation (git blob / content hash)
├── renderer.py        # Markdown → per-block HTML renderer (markdown-it-py)
├── view_specs.py      # Row/TOC/header/slide render specs — shared by the Jinja
│                      # render and the in-browser Pyodide soft swap
├── comments_cli.py    # Thin CLI over the JSON comment API
├── list_comments.py   # Offline comment listing, straight from the DB, with
│                      # git-blame line relocation
├── parity_fixture.py  # Canonical fixture for server/client render parity
├── templates/         # Jinja2 templates
│   ├── base.html
│   ├── index.html     # File browser
│   └── view.html      # File viewer with comment UI
├── static/
│   ├── style.css      # Responsive CSS
│   └── app.js         # Comment interaction + presentation mode
├── test_db.py         # Data layer tests
├── test_file_id.py    # File ID derivation tests
├── test_renderer.py   # Renderer tests
├── test_presentation.py # Marp presentation mode + anchor-parity tests
├── test_view_specs.py # Render-spec builder tests
├── test_comments_cli.py # CLI tests
├── test_parity.py     # Server/client render parity
├── test_server.py     # Route-level tests
└── README.md          # This file
```

## Assumptions

- **Markdown-only in v1.** Org-mode rendering deferred to a follow-up.
- **File/directory selection** via CLI argument to `server.py`.
- **No auth** in first cut — single-user / trusted-network deployment.
</content>
