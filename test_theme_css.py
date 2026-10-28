"""Dark mode wiring (DR-8): stylesheet, header toggle, and mermaid theme.

The switching logic lives in ``static/theme.js`` and is exercised by
``test_theme.js``.  These tests pin the parts a browser would otherwise be
needed to see: that every colour in the app's own chrome comes from a themed
variable, and that the pages load the switch early enough to avoid a flash.
"""

import re
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app, configure
from view_specs import PRESENTATION_THEMES

HERE = Path(__file__).parent
CSS = (HERE / "static" / "style.css").read_text()

COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(")

# Named deck themes are an author's chosen palette, so they keep it in both
# modes.  Only "default" follows the reader's theme.
NAMED_DECK_THEMES = [t for t in PRESENTATION_THEMES if t != "default"]


def rules(css):
    """Yield (selector, body) for every innermost ``{...}`` block."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    stack = []
    start = 0
    for i, ch in enumerate(css):
        if ch == "{":
            stack.append((css[start:i].strip(), i + 1))
            start = i + 1
        elif ch == "}":
            selector, body_start = stack.pop()
            body = css[body_start:i]
            if "{" not in body:
                yield selector.split(";")[-1].strip(), body
            start = i + 1
        elif ch == ";" and not stack:
            start = i + 1


def named_theme_rule(selector):
    return all(
        any(f".theme-{t}" in part for t in NAMED_DECK_THEMES)
        for part in selector.split(",")
    )


ROOT_BODY = next(body for sel, body in rules(CSS) if sel == ":root")


class TestPalette:
    def test_root_follows_the_os_by_default(self):
        assert re.search(r"color-scheme:\s*light dark", ROOT_BODY)

    @pytest.mark.parametrize("mode", ["light", "dark"])
    def test_a_chosen_theme_overrides_the_os(self, mode):
        bodies = [b for s, b in rules(CSS) if s == f':root[data-theme="{mode}"]']
        assert bodies and re.search(rf"color-scheme:\s*{mode}\s*;", bodies[0])

    def test_every_colour_variable_has_a_dark_value(self):
        decls = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", ROOT_BODY))
        colours = {k: v for k, v in decls.items() if COLOR.search(v)}
        assert len(colours) >= 8
        for name, value in colours.items():
            assert value.strip().startswith("light-dark("), name

    def test_every_variable_used_is_defined(self):
        """A variable with no fallback must come from :root (a fallback marks
        one that JS supplies, like deck_dom.js's --stage-scale)."""
        defined = set(re.findall(r"(--[\w-]+)\s*:", ROOT_BODY))
        used = set(re.findall(r"var\((--[\w-]+)\)", CSS))
        assert used - defined == set()

    def test_app_chrome_has_no_hardcoded_colours(self):
        """Every colour outside :root and the named deck themes is a variable.

        A literal here would stay light in dark mode.  The allowed exceptions
        are white glyphs on a filled control, and the deck's diagram box,
        which is white in every deck theme.
        """
        allowed = {
            ".marker-btn", '.comment-form button[type="submit"]',
            ".presentation-control", ".view-toggle",
            ".presentation .slide .mermaid",
            ".presentation.theme-default .slide .mermaid",
        }
        offenders = []
        for sel, body in rules(CSS):
            if sel.startswith(":root") or sel.startswith("@font-face"):
                continue
            if named_theme_rule(sel) or sel in allowed:
                continue
            if COLOR.search(body):
                offenders.append(sel)
        assert offenders == []

    @pytest.mark.parametrize("name", NAMED_DECK_THEMES)
    def test_named_deck_themes_keep_their_palette(self, name):
        pinned = [
            b for s, b in rules(CSS)
            if f".presentation.theme-{name}" in [p.strip() for p in s.split(",")]
            and "color-scheme" in b
        ]
        assert pinned and re.search(r"color-scheme:\s*light\s*;", pinned[0])


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "a.md").write_text("# A\n\n```mermaid\ngraph TD\n  A-->B\n```\n")
        configure(td, Path(td) / "c.db")
        yield TestClient(app)


class TestPages:
    @pytest.mark.parametrize("url", ["/", "/view?path=a.md"])
    def test_theme_script_runs_before_first_paint(self, client, url):
        html = client.get(url).text
        head = html.split("</head>")[0]
        tag = re.search(r'<script src="/static/theme\.js\?v=\w+"></script>', head)
        assert tag, "theme.js must be a plain blocking script in <head>"
        assert head.index("theme.js") < head.index("style.css")

    @pytest.mark.parametrize("url", ["/", "/view?path=a.md"])
    def test_header_has_the_toggle(self, client, url):
        html = client.get(url).text
        header = html.split("<header>")[1].split("</header>")[0]
        assert 'id="theme-toggle"' in header
        assert 'type="button"' in header

    def test_theme_script_is_served(self, client):
        r = client.get("/static/theme.js")
        assert r.status_code == 200
        assert "install" in r.text

    def test_review_diagrams_follow_the_theme(self, client):
        html = client.get("/view?path=a.md").text
        assert "theme: 'default'" not in html
        assert "docReviewTheme.mermaidTheme(" in html

    def test_a_switch_redraws_only_the_review(self, client):
        """The deck keeps mermaid's light theme on its white box, so the
        redraw is scoped to .view-layout.  That only works because app.js
        mounts the deck on <body>, outside .view-layout."""
        html = client.get("/view?path=a.md").text
        handler = html[html.index("docReviewTheme.onChange("):]
        handler = handler[: handler.index("});")]
        assert "querySelector('.view-layout')" in handler
        assert "initMermaid(review)" in handler
        assert "initMermaid()" not in handler
        app_js = (HERE / "static" / "app.js").read_text()
        assert "document.body.appendChild(deckEl)" in app_js
        # And the deck is decided by the root, never by the page theme.
        assert "classList.contains('presentation')" in html

    def test_the_switch_listener_is_registered_before_the_first_render(self, client):
        html = client.get("/view?path=a.md").text
        assert html.index("docReviewTheme.onChange(") < html.index("await initMermaid();")
        assert "if (window.docReviewTheme)" in html


class TestSlideFit:
    def test_slides_centre_safely(self):
        """DR-10: an overflowing slide must spill downwards only.

        A slide is an `overflow: auto` flex box that centres its content.
        Plain `center` spills an overflow equally above and below, and the
        part above the top edge can be neither scrolled to nor counted by
        `scrollHeight`.  The fit check in deck_dom.js measures
        `scrollHeight`, so plain `center` would make it report half the
        overflow and hide the top of the slide besides.  `safe center` looks
        the same on every slide that fits and falls back to the start edge on
        one that does not.
        """
        offenders = []
        for sel, body in rules(CSS):
            if not re.search(r"\.slide(?![-\w])", sel):
                continue
            for prop in ("justify-content", "align-items"):
                # The last declaration wins; a plain `center` before it is
                # the fallback for a browser that rejects `safe`.
                values = re.findall(prop + r"\s*:\s*([^;]+);", body)
                if values and "center" in values[-1] and "safe" not in values[-1]:
                    offenders.append((sel, prop))
        assert offenders == []
