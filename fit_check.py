#!/usr/bin/env python3
"""List the slides of a deck that overflow the 1920x1080 stage (DR-13).

    python3 fit_check.py drafts/presentation-12-talk.md
    python3 fit_check.py --json --base http://127.0.0.1:28080 deck.md

This is the bot's check before it sends a deck.  It opens the deck on a running
doc-review server in headless Chromium, presses Present, waits for fonts,
images and diagrams, and reads the answer from deck_dom.js checkFit().  The
measurement is the browser's own layout, never a second copy of it: a copy in
Python would drift from what Chaos sees, which is the drift view_specs.py
exists to prevent.

Chromium is driven over --remote-debugging-pipe, so the script needs nothing
beyond the standard library and a browser on PATH.  The page boots Pyodide and
mermaid from their CDNs, so it needs the network too.

Exit codes: 0 every slide fits, 1 some slide overflows, 2 nothing could be
measured (no browser, no Present button, a timeout).
"""

import argparse
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

STAGE = "1920x1080"
DEFAULT_BASE = "http://127.0.0.1:28080"

EXIT_FITS = 0
EXIT_OVERFLOW = 1
EXIT_UNMEASURED = 2

# Pyodide's first boot fetches tens of megabytes, so the default is generous.
DEFAULT_TIMEOUT = 180
EVALUATE_SLACK = 10


class MeasureError(Exception):
    """The deck could not be measured, so no answer about its fit exists."""


# ── CDP over a pipe ─────────────────────────────────────────────────────────
# Chromium reads commands on fd 3 and writes replies and events on fd 4, each
# message one JSON object ended by a NUL byte.


def encode_frame(message):
    return json.dumps(message).encode("utf-8") + b"\0"


class FrameDecoder:
    """Turns the bytes read off fd 4 into messages.

    A read can end mid-message, or mid-character, so bytes are held until
    their NUL arrives and only then decoded.
    """

    def __init__(self):
        self._buf = b""

    def feed(self, data):
        self._buf += data
        *frames, self._buf = self._buf.split(b"\0")
        return [json.loads(f.decode("utf-8")) for f in frames if f]


class Browser:
    """One headless Chromium and one page in it, spoken to over CDP."""

    def __init__(self, chromium, timeout):
        self.timeout = timeout
        self._next_id = 0
        self._decoder = FrameDecoder()
        self._events = []
        self._profile = tempfile.TemporaryDirectory(prefix="fit-check-")
        to_child_r, self._to_child = os.pipe()
        self._from_child, from_child_w = os.pipe()
        width, height = STAGE.split("x")
        args = [
            "--headless",
            # The container runs as a user with no namespaces to sandbox in.
            "--no-sandbox",
            # /dev/shm in a container is 64MB, which a deck page outgrows.
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--remote-debugging-pipe",
            f"--user-data-dir={self._profile.name}",
            f"--window-size={width},{height}",
            "about:blank",
        ]
        # The shell puts the two pipe ends on fds 3 and 4, where Chromium
        # looks for them, then gets out of the way.
        shim = f'exec "$0" "$@" 3<&{to_child_r} 4>&{from_child_w}'
        self._proc = subprocess.Popen(
            ["sh", "-c", shim, chromium, *args],
            pass_fds=(to_child_r, from_child_w),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(to_child_r)
        os.close(from_child_w)
        self._session = None

    def close(self):
        try:
            if self._proc.poll() is None:
                try:
                    self.send("Browser.close", session=False, timeout=5)
                    self._proc.wait(timeout=5)
                except Exception:
                    self._proc.kill()
                    self._proc.wait()
        finally:
            for fd in (self._to_child, self._from_child):
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._profile.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, deadline):
        left = deadline - time.monotonic()
        if left <= 0:
            raise MeasureError(f"timed out after {self.timeout}s")
        ready, _, _ = select.select([self._from_child], [], [], left)
        if not ready:
            raise MeasureError(f"timed out after {self.timeout}s")
        data = os.read(self._from_child, 1 << 16)
        if not data:
            raise MeasureError("Chromium exited before answering")
        return self._decoder.feed(data)

    def send(self, method, params=None, session=None, timeout=None):
        """Send one command and return its result.

        Commands go to the attached page unless `session` is False, which
        addresses the browser itself.
        """
        self._next_id += 1
        msg = {"id": self._next_id, "method": method, "params": params or {}}
        if session is not False and self._session:
            msg["sessionId"] = self._session
        try:
            os.write(self._to_child, encode_frame(msg))
        except OSError as err:
            # Python ignores SIGPIPE, so a browser that has exited shows up
            # here as BrokenPipeError rather than as EOF on the read side.
            raise MeasureError(f"Chromium is not running ({err})") from None
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            reply = None
            # One read can carry the reply and the events after it, such as
            # Page.loadEventFired right behind Page.navigate's reply.  Every
            # frame that is not the reply is kept for wait_event.
            for frame in self._read(deadline):
                if reply is None and frame.get("id") == msg["id"]:
                    reply = frame
                else:
                    self._events.append(frame)
            if reply is None:
                continue
            if "error" in reply:
                raise MeasureError(f"{method}: {reply['error'].get('message')}")
            return reply.get("result", {})

    def wait_event(self, method, deadline):
        while True:
            for i, ev in enumerate(self._events):
                if ev.get("method") == method:
                    return self._events.pop(i)
            self._events.extend(self._read(deadline))

    def open(self, url):
        targets = self.send("Target.getTargets", session=False)["targetInfos"]
        page = next((t for t in targets if t["type"] == "page"), None)
        if page is None:
            page = self.send("Target.createTarget", {"url": "about:blank"},
                             session=False)
        self._session = self.send(
            "Target.attachToTarget",
            {"targetId": page["targetId"], "flatten": True},
            session=False,
        )["sessionId"]
        width, height = (int(n) for n in STAGE.split("x"))
        self.send("Emulation.setDeviceMetricsOverride", {
            "width": width, "height": height,
            "deviceScaleFactor": 1, "mobile": False,
        })
        self.send("Page.enable")
        deadline = time.monotonic() + self.timeout
        nav = self.send("Page.navigate", {"url": url})
        if nav.get("errorText"):
            raise MeasureError(f"could not load {url}: {nav['errorText']}")
        self.wait_event("Page.loadEventFired", deadline)

    def evaluate(self, expression):
        result = self.send("Runtime.evaluate", {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
        # The page script bounds its own waits by the same timeout and then
        # says what it was waiting for.  The slack lets that answer arrive
        # before the pipe gives up with a bare "timed out".
        }, timeout=self.timeout + EVALUATE_SLACK)
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise MeasureError(f"page script failed: {text}")
        return result["result"].get("value")


# ── The measurement ─────────────────────────────────────────────────────────
# Runs in the page.  Every wait is bounded by `ms`, and a failure comes back
# as {error} rather than a throw, so the message reaches the report intact.

MEASURE_JS = r"""
(async (ms) => {
    const until = Date.now() + ms;
    const sleep = (t) => new Promise((r) => setTimeout(r, t));
    const btn = document.getElementById("present-toggle");
    if (!btn) return {error: "no Present button on the page: is it a doc-review /view page?"};
    // The button is shown once the Pyodide renderer is warm and the
    // document is a deck.
    while (btn.hidden) {
        if (Date.now() > until) return {error: "the Present button never appeared: "
            + "the deck needs `marp: true` in its front matter, and the page needs "
            + "the Pyodide CDN"};
        await sleep(250);
    }
    btn.click();
    const deck = document.querySelector(".presentation");
    if (!deck) return {error: "pressing Present built no deck"};
    if (document.fonts && document.fonts.ready) await document.fonts.ready;
    const imgs = Array.from(deck.querySelectorAll("img"));
    await Promise.all(imgs.map((img) => img.complete ? null : new Promise((r) => {
        img.addEventListener("load", r, {once: true});
        img.addEventListener("error", r, {once: true});
    })));
    // A diagram is drawn when its container holds the SVG.  One that never
    // draws keeps its source text, and the report says so.
    const undrawn = () => Array.from(deck.querySelectorAll(".mermaid"))
        .filter((el) => !el.querySelector("svg")).length;
    while (undrawn() && Date.now() < until) await sleep(250);
    // Let the layout settle after the last diagram lands.
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const d = window.docReviewDeck;
    const over = d.checkFit(document, deck);
    if (over === null) return {error: "the deck was not laid out, so nothing was measured"};
    const sections = deck.querySelectorAll("section.slide");
    const overflowing = over.map((n) => {
        const s = sections[n - 1];
        const wasHidden = s.hidden;
        s.hidden = false;
        const px = d.slideOverflow(s);
        s.hidden = wasHidden;
        return {slide: n, px: px};
    });
    return {slides: sections.length, overflowing: overflowing,
            undrawn_diagrams: undrawn()};
})
"""


def view_url(base, path):
    return base.rstrip("/") + "/view?" + urllib.parse.urlencode({"path": path})


def check_page(url):
    """Fail early, and by status, on a page the server will not serve.

    A 404 page has no Present button either, and blaming the button would
    send the reader to the front matter instead of the path.
    """
    try:
        with urllib.request.urlopen(url, timeout=30):
            pass
    except urllib.error.HTTPError as err:
        raise MeasureError(f"the server answered {err.code} for {url}") from None
    except (urllib.error.URLError, OSError) as err:
        reason = getattr(err, "reason", err)
        raise MeasureError(f"could not reach {url}: {reason}") from None


def find_chromium(explicit):
    for name in ([explicit] if explicit else ["chromium", "chromium-browser",
                                              "google-chrome"]):
        found = shutil.which(name)
        if found:
            return found
    raise MeasureError("no Chromium on PATH; pass --chromium")


def measure(base, path, chromium=None, timeout=DEFAULT_TIMEOUT):
    """Open `path` on the server at `base` and return the fit measurement."""
    binary = find_chromium(chromium)
    url = view_url(base, path)
    check_page(url)
    with Browser(binary, timeout) as browser:
        browser.open(url)
        result = browser.evaluate(f"({MEASURE_JS})({int(timeout * 1000)})")
    if not isinstance(result, dict):
        raise MeasureError(f"unexpected answer from the page: {result!r}")
    if "error" in result:
        raise MeasureError(result["error"])
    return result


def report(path, result, as_json=False):
    """Return the text to print and the exit code for one measurement."""
    over = result["overflowing"]
    code = EXIT_OVERFLOW if over else EXIT_FITS
    if as_json:
        return json.dumps({"path": path, **result}), code
    total = result["slides"]
    if over:
        lines = [f"{path}: {len(over)} of {total} slides overflow the {STAGE} stage"]
        lines += [f"  slide {o['slide']}: over by {o['px']}px" for o in over]
    else:
        lines = [f"{path}: all {total} slides fit the {STAGE} stage"]
    undrawn = result.get("undrawn_diagrams") or 0
    if undrawn:
        noun = "diagram" if undrawn == 1 else "diagrams"
        lines.append(f"  warning: {undrawn} {noun} did not draw, so "
                     "the slides holding them were measured as source text")
    return "\n".join(lines), code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("path", help="the deck's path as doc-review serves it")
    parser.add_argument("--base", default=DEFAULT_BASE,
                        help=f"doc-review server (default {DEFAULT_BASE})")
    parser.add_argument("--chromium", help="browser binary (default: PATH)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"seconds to wait for the deck (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--json", action="store_true", help="print the measurement")
    args = parser.parse_args(argv)
    try:
        result = measure(args.base, args.path, args.chromium, args.timeout)
    except Exception as err:
        # Exit 1 means "a slide overflows", and an uncaught exception also
        # exits 1, so every failure to measure is caught and named here.
        kind = "" if isinstance(err, MeasureError) else f"{type(err).__name__}: "
        print(f"{args.path}: not measured: {kind}{err}", file=sys.stderr)
        return EXIT_UNMEASURED
    text, code = report(args.path, result, as_json=args.json)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
