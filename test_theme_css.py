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
            # align-content: a two-column slide is a grid (DR-11), whose rows
            # centre on that axis instead.
            for prop in ("justify-content", "align-items", "align-content"):
                # The last declaration wins; a plain `center` before it is
                # the fallback for a browser that rejects `safe`.
                values = re.findall(prop + r"\s*:\s*([^;]+);", body)
                if values and "center" in values[-1] and "safe" not in values[-1]:
                    offenders.append((sel, prop))
        assert offenders == []

    def test_viewers_never_see_the_fit_check(self):
        """DR-16: whoever opens a deck is a viewer, not its author.

        The author is an agent, and fit_check.py tells it which slides
        overflow.  A flag on the slide only ever reached the viewer, who
        cannot fix it, so no page runs the check and no style draws one.
        """
        static = HERE / "static"
        for name in ("app.js", "standalone.js"):
            src = (static / name).read_text()
            # The helpers DR-10 put in the pages, by name, so a caller left
            # behind is caught here: no JS test drives app.js into a deck.
            for gone in ("checkFit", "checkDeckFit", "flagSlide", "watchFit",
                         "fit-flag"):
                assert gone not in src, (name, gone)
        assert "fit-flag" not in CSS
        assert not any(".overflowing" in sel for sel, _ in rules(CSS))


def _px(body, prop):
    """The last ``prop: Npx`` declared in *body*, as a number, or 0."""
    values = re.findall(r"(?<![-\w])" + prop + r"\s*:\s*(\d+)px", body)
    return int(values[-1]) if values else 0


def _rule(selector):
    """Every declaration aimed at *selector*, grouped rules included, in order."""
    return ";".join(
        body
        for sel, body in rules(CSS)
        if selector in (part.strip() for part in sel.split(","))
    )


class TestSlideChrome:
    """DR-12: the top bar and the footer line sit in the slide's own padding,
    so they never cover a row and the fit check (DR-10) never counts them."""

    SLIDE = ".presentation .slide"

    def _vertical_padding(self):
        values = re.findall(r"padding\s*:\s*(\d+)px", _rule(self.SLIDE))
        return int(values[-1])

    @pytest.mark.parametrize("band, edge", [("header", "top"), ("footer", "bottom")])
    def test_the_band_fits_in_the_slide_padding(self, band, edge):
        body = _rule(f".presentation .slide-{band}")
        assert re.search(r"position\s*:\s*absolute", body)
        depth = (
            _px(body, edge)
            + _px(body, "line-height")
            + _px(body, "padding-top")
            + _px(body, "padding-bottom")
            + _px(body, f"border-{'bottom' if edge == 'top' else 'top'}")
        )
        assert 0 < depth <= self._vertical_padding()

    @pytest.mark.parametrize("band", ["header", "footer"])
    def test_a_long_title_cannot_widen_the_slide(self, band):
        """A title wider than the slide is clipped.  Unclipped, it would grow
        the slide's scrollWidth, and the fit check would flag every slide."""
        body = _rule(f".presentation .slide-{band}")
        assert re.search(r"white-space\s*:\s*nowrap", body)
        assert re.search(r"overflow\s*:\s*hidden", body)

    @pytest.mark.parametrize("band", ["header", "footer"])
    def test_the_band_does_not_follow_the_slide_alignment(self, band):
        """Title, centered and big-number slides and the uncover theme centre
        their text; the frame stays put on every slide."""
        assert re.search(r"text-align\s*:\s*left", _rule(f".presentation .slide-{band}"))

    @pytest.mark.parametrize("band", ["header", "footer"])
    def test_the_band_clears_the_side_padding(self, band):
        body = _rule(f".presentation .slide-{band}")
        side = int(re.findall(r"padding\s*:\s*\d+px\s+(\d+)px", _rule(self.SLIDE))[-1])
        assert _px(body, "left") == side and _px(body, "right") == side


class TestLayoutDisplay:
    def test_a_layout_that_sets_display_still_hides_a_hidden_slide(self):
        """DR-11: `.presentation .slide.layout-X { display: grid }` has the
        same specificity as `.presentation .slide[hidden] { display: none }`
        and comes later, so it would show every slide of that layout at once.
        Such a rule must exclude hidden slides itself."""
        on_the_slide = re.compile(r"\.slide\.layout-[\w-]+(?P<guard>:not\(\[hidden\]\))?$")
        offenders = [
            part.strip()
            for sel, body in rules(CSS)
            if re.search(r"(^|[;\s])display\s*:", body)
            for part in sel.split(",")
            if (m := on_the_slide.search(part.strip())) and not m.group("guard")
        ]
        assert offenders == []

    @pytest.mark.parametrize("region", ["head", "left", "right"])
    def test_every_region_is_styled(self, region):
        """The region names view_specs emits for a two-column slide."""
        assert any(
            ".region-%s" % region in sel and body.strip() for sel, body in rules(CSS)
        )


def _specificity(selector):
    """(classes + attributes + pseudo-classes, elements) of one selector."""
    sel = re.sub(r"::[\w-]+", "", selector)
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]|:(?!not\b)[\w-]+", sel))
    elements = len(re.findall(r"(?:^|[\s>+~(])([a-z][\w-]*)", sel))
    return classes, elements


class TestLayoutHeadingSize:
    @pytest.mark.parametrize("layout", ["section", "big-number"])
    def test_the_layout_heading_size_beats_every_theme(self, layout):
        """DR-11 review: four named themes size `.slide h1` at the same
        specificity as a layout rule, later in the file, so a big-number
        figure came out at the theme's 3em instead of its own 9em."""
        def sizing(pattern):
            return [
                _specificity(part.strip())
                for sel, body in rules(CSS)
                if re.search(r"font-size\s*:", body)
                for part in sel.split(",")
                if re.search(pattern, part.strip())
            ]

        theirs = sizing(r"\.theme-[\w-]+ \.slide h1$")
        ours = sizing(r"\.slide\.layout-%s h1$" % layout)
        assert theirs and ours
        assert min(ours) > max(theirs)
