"""Tests for fit_check.py, the bot-side deck fit check (DR-13).

The measurement itself is deck_dom.js checkFit(), covered by test_deck_dom.js.
What is tested here is the part of the script a browser is not needed for: the
CDP pipe framing, the page url, and how a measurement becomes a report and an
exit code.  The end-to-end run needs Chromium and the Pyodide CDN, so it is
skipped unless both a browser and DOC_REVIEW_FIT_URL are present.
"""

import json
import os
import shutil
import sys
import textwrap

import pytest

import fit_check
from fit_check import (
    EXIT_FITS,
    EXIT_OVERFLOW,
    EXIT_UNMEASURED,
    FrameDecoder,
    MeasureError,
    encode_frame,
    find_chromium,
    report,
    view_url,
)


class TestFraming:
    """--remote-debugging-pipe carries JSON messages, each ended by a NUL."""

    def test_encode_ends_with_nul(self):
        frame = encode_frame({"id": 1, "method": "Page.enable"})
        assert frame.endswith(b"\0")
        assert json.loads(frame[:-1]) == {"id": 1, "method": "Page.enable"}

    def test_decoder_splits_two_messages_in_one_read(self):
        dec = FrameDecoder()
        got = dec.feed(b'{"id":1}\0{"id":2}\0')
        assert got == [{"id": 1}, {"id": 2}]

    def test_decoder_holds_a_partial_message(self):
        dec = FrameDecoder()
        assert dec.feed(b'{"id":') == []
        assert dec.feed(b'7}\0{"id"') == [{"id": 7}]
        assert dec.feed(b':8}\0') == [{"id": 8}]

    def test_decoder_keeps_utf8_split_across_reads(self):
        # Chromium sends raw UTF-8, not \u escapes.
        raw = '{"t": "slide — one"}'.encode("utf-8") + b"\0"
        cut = raw.index(b"\xe2") + 1  # inside the em dash's three bytes
        dec = FrameDecoder()
        assert dec.feed(raw[:cut]) == []
        assert dec.feed(raw[cut:]) == [{"t": "slide — one"}]


class TestViewUrl:
    def test_path_is_query_encoded(self):
        url = view_url("http://127.0.0.1:28080/", "drafts/presentation-1 a&b.md")
        assert url == (
            "http://127.0.0.1:28080/view?path=drafts%2Fpresentation-1+a%26b.md"
        )

    def test_base_without_trailing_slash(self):
        assert view_url("http://h:1", "d.md") == "http://h:1/view?path=d.md"


class TestReport:
    def test_clean_deck_exits_zero(self):
        text, code = report("d.md", {"slides": 4, "overflowing": []})
        assert code == EXIT_FITS
        assert text == "d.md: all 4 slides fit the 1920x1080 stage"

    def test_overflow_lists_each_slide_and_exits_one(self):
        result = {
            "slides": 9,
            "overflowing": [{"slide": 3, "px": 140}, {"slide": 7, "px": 22}],
        }
        text, code = report("d.md", result)
        assert code == EXIT_OVERFLOW
        assert text.splitlines() == [
            "d.md: 2 of 9 slides overflow the 1920x1080 stage",
            "  slide 3: over by 140px",
            "  slide 7: over by 22px",
        ]

    def test_undrawn_diagrams_are_named(self):
        # A diagram that never drew is measured as its source text, which is
        # usually shorter, so the result must say it is provisional.
        result = {"slides": 2, "overflowing": [], "undrawn_diagrams": 1}
        text, code = report("d.md", result)
        assert code == EXIT_FITS
        assert "1 diagram did not draw" in text

    def test_json_output_is_the_measurement(self):
        result = {"slides": 2, "overflowing": [{"slide": 1, "px": 5}]}
        text, code = report("d.md", result, as_json=True)
        assert json.loads(text) == {"path": "d.md", **result}
        assert code == EXIT_OVERFLOW


class TestMain:
    def test_measure_error_exits_two(self, monkeypatch, capsys):
        def boom(*a, **k):
            raise MeasureError("no Present button: is `marp: true` set?")

        monkeypatch.setattr(fit_check, "measure", boom)
        code = fit_check.main(["d.md"])
        assert code == EXIT_UNMEASURED
        assert "no Present button" in capsys.readouterr().err

    def test_overflow_exit_code_reaches_main(self, monkeypatch, capsys):
        monkeypatch.setattr(
            fit_check, "measure",
            lambda *a, **k: {"slides": 1, "overflowing": [{"slide": 1, "px": 9}]},
        )
        assert fit_check.main(["d.md"]) == EXIT_OVERFLOW
        assert "slide 1: over by 9px" in capsys.readouterr().out

    def test_missing_page_names_the_status(self, monkeypatch):
        # A 404 page has no Present button either, and "no button" would send
        # whoever reads it to the front matter instead of the path.
        import urllib.error
        import urllib.request

        def not_found(url, timeout=None):
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)

        monkeypatch.setattr(urllib.request, "urlopen", not_found)
        with pytest.raises(MeasureError, match="404"):
            fit_check.check_page("http://h:1/view?path=gone.md")

    def test_missing_browser_is_a_measure_error(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(MeasureError, match="no Chromium"):
            find_chromium(None)


# A stand-in browser speaking the pipe protocol on fds 3 and 4.  It answers
# Page.navigate and sends Page.loadEventFired in ONE write, the order real
# Chromium uses, so a reader that drops frames after a reply loses the event.
FAKE_BROWSER = textwrap.dedent('''\
    #!{python}
    import json, os, sys
    buf = b""
    def out(*msgs):
        os.write(4, b"".join(json.dumps(m).encode() + b"\\0" for m in msgs))
    while True:
        data = os.read(3, 65536)
        if not data:
            sys.exit(0)
        buf += data
        *frames, buf = buf.split(b"\\0")
        for f in frames:
            m = json.loads(f)
            meth, i = m["method"], m["id"]
            if meth == "Target.getTargets":
                out({{"id": i, "result": {{"targetInfos": [
                    {{"type": "page", "targetId": "T1"}}]}}}})
            elif meth == "Target.attachToTarget":
                out({{"id": i, "result": {{"sessionId": "S1"}}}})
            elif meth == "Page.navigate":
                out({{"id": i, "result": {{}}}},
                    {{"method": "Page.loadEventFired", "params": {{}}}})
            elif meth == "Runtime.evaluate":
                out({{"id": i, "result": {{"result": {{"value": {{
                    "slides": 1, "overflowing": []}}}}}}}})
            elif meth == "Browser.close":
                out({{"id": i, "result": {{}}}})
                sys.exit(0)
            else:
                out({{"id": i, "result": {{}}}})
''')


def _fake(tmp_path, body):
    path = tmp_path / "fake-chromium"
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


class TestBrowserPipe:
    def test_event_in_the_same_read_as_its_reply_is_kept(self, tmp_path):
        fake = _fake(tmp_path, FAKE_BROWSER.format(python=sys.executable))
        with fit_check.Browser(fake, timeout=5) as browser:
            browser.open("http://h:1/view?path=d.md")
            assert browser.evaluate("1") == {"slides": 1, "overflowing": []}

    def test_browser_that_dies_at_start_is_unmeasured(self, tmp_path):
        # Exit 1 means "a slide overflows", so a dead browser must not reach
        # it as an uncaught BrokenPipeError.
        fake = _fake(tmp_path, f"#!{sys.executable}\nimport sys; sys.exit(3)\n")
        browser = fit_check.Browser(fake, timeout=5)
        browser._proc.wait()
        try:
            with pytest.raises(MeasureError):
                browser.open("http://h:1/view?path=d.md")
        finally:
            browser.close()

    def test_unexpected_error_in_main_is_unmeasured(self, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("anything at all")

        monkeypatch.setattr(fit_check, "measure", boom)
        assert fit_check.main(["d.md"]) == EXIT_UNMEASURED


@pytest.mark.skipif(
    not (shutil.which("chromium") and all(os.environ.get(v) for v in (
        "DOC_REVIEW_FIT_URL", "DOC_REVIEW_FIT_PATH", "DOC_REVIEW_FIT_CLEAN"))),
    reason="needs chromium on PATH and the three DOC_REVIEW_FIT_* variables",
)
class TestLive:
    """Against a running server, which fetches Pyodide from its CDN.

    DOC_REVIEW_FIT_PATH names a deck known to overflow on exactly one slide,
    and DOC_REVIEW_FIT_CLEAN one known to fit.
    """

    def test_overflowing_deck(self):
        result = fit_check.measure(
            os.environ["DOC_REVIEW_FIT_URL"], os.environ["DOC_REVIEW_FIT_PATH"]
        )
        assert [o["slide"] for o in result["overflowing"]] == [2]
        assert result["overflowing"][0]["px"] > 0

    def test_clean_deck(self):
        result = fit_check.measure(
            os.environ["DOC_REVIEW_FIT_URL"], os.environ["DOC_REVIEW_FIT_CLEAN"]
        )
        assert result["overflowing"] == []
        assert result["slides"] >= 2
