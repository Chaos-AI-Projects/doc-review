"""Tests for Marp-format presentation mode (#452).

Presentation mode is a **grouping + CSS layer over the same block list** review
mode renders.  There is no second parse path and no second markup path: the
slides are built from the very ``source_row_specs()`` dicts the Jinja render and
the soft swap already use.

That is what makes the anchor-parity guarantee in ``TestAnchorParity`` hold *by
construction* rather than by coincidence — and anchor parity is the whole point.
A comment is attached to ``id="L{start_line}"``; if a mode flip moved a block's
line range, every comment on that block would orphan (cf. the 2026-07-22
comment-loss incident).
"""

import pytest

from renderer import _COMMENTS_ONLY_RE, render_markdown_blocks
from view_specs import (
    PRESENTATION_LAYOUTS,
    _is_all_comments,
    comment_directives,
    front_matter_directives,
    presentation_specs,
    slide_specs,
    source_row_specs,
)

# A Marp-shaped document exercising all three gotchas from the issue:
# global front matter (1), no per-slide directives yet (2), and a `---`
# directly under a text line, which CommonMark reads as a setext heading
# underline rather than a thematic break (3).
MARP_DOC = "\n".join(
    [
        "---",                  # 1
        "marp: true",           # 2
        "theme: default",       # 3
        "paginate: true",       # 4
        "---",                  # 5
        "",                     # 6
        "# Slide one",          # 7
        "",                     # 8
        "body text",            # 9
        "",                     # 10
        "---",                  # 11
        "",                     # 12
        "# Slide two",          # 13
        "",                     # 14
        "- a",                  # 15
        "- b",                  # 16
        "",                     # 17
        "---",                  # 18
        "",                     # 19
        "# Slide three",        # 20
        "",                     # 21
        "tail",                 # 22
        "---",                  # 23
        "not a break",          # 24
        "",
    ]
)

PLAIN_DOC = "# Title\n\nJust a paragraph.\n\n## Section\n\nMore text.\n"


@pytest.fixture
def marp_blocks():
    return render_markdown_blocks(MARP_DOC)


@pytest.fixture
def plain_blocks():
    return render_markdown_blocks(PLAIN_DOC)


def _rows(slides):
    """Every row spec across all slides, in document order."""
    return [row for slide in slides for row in slide["rows"]]


class TestFrontMatter:
    """Gotcha 1: without the ``front_matter`` plugin, ``---\\nmarp: true\\n---``
    parses as a thematic break plus a setext ``<h2>`` and renders as visible
    junk at the top of every deck."""

    def test_front_matter_is_one_block_not_an_hr_plus_heading(self, marp_blocks):
        first = marp_blocks[0]
        assert (first["start_line"], first["end_line"]) == (1, 5)

    def test_front_matter_does_not_render_as_a_heading(self, marp_blocks):
        """It renders as metadata, not as `<hr>` + a bogus setext `<h2>`."""
        assert marp_blocks[0]["type"] == "front_matter"
        assert "<h2>" not in marp_blocks[0]["html"]
        assert marp_blocks[0]["html"] == (
            '<pre class="front-matter">marp: true\ntheme: default\n'
            "paginate: true</pre>"
        )

    def test_front_matter_content_is_escaped(self):
        """It is shown verbatim, so it must not be able to inject markup."""
        blocks = render_markdown_blocks(
            '---\ntitle: <img src=x onerror="alert(1)">\n---\n\n# T\n'
        )
        assert "<img" not in blocks[0]["html"]
        assert "&lt;img" in blocks[0]["html"]

    def test_front_matter_does_not_split_a_slide(self, marp_blocks):
        """Its closing `---` is consumed as metadata, not read as a break."""
        assert len(slide_specs(marp_blocks)) == 3

    def test_directives_are_parsed_as_config(self):
        assert front_matter_directives(MARP_DOC) == {
            "marp": "true",
            "theme": "default",
            "paginate": "true",
        }

    def test_no_front_matter_no_directives(self):
        assert front_matter_directives(PLAIN_DOC) == {}

    def test_a_leading_thematic_break_is_not_front_matter(self):
        """`---` followed by a blank line opens no metadata block."""
        assert front_matter_directives("---\n\n# Title\n") == {}

    def test_directive_values_keep_inner_colons(self):
        assert front_matter_directives("---\ntitle: a: b\n---\n") == {
            "title": "a: b"
        }


class TestSlideGrouping:
    def test_slides_split_on_thematic_breaks(self, marp_blocks):
        assert len(slide_specs(marp_blocks)) == 3

    def test_slides_are_numbered_from_one(self, marp_blocks):
        slides = slide_specs(marp_blocks)
        assert [s["index"] for s in slides] == [0, 1, 2]
        assert [s["number"] for s in slides] == [1, 2, 3]

    def test_each_slide_holds_the_blocks_between_its_breaks(self, marp_blocks):
        slides = slide_specs(marp_blocks)
        assert [[r["startLine"] for r in s["rows"]] for s in slides] == [
            [7, 9],
            [13, 15],
            [20, 22, 24],
        ]

    def test_the_break_itself_is_not_slide_content(self, marp_blocks):
        """The `<hr>` rows are delimiters; they must not show up on a slide."""
        assert "<hr>" not in [r["html"] for r in _rows(slide_specs(marp_blocks))]

    def test_setext_underline_does_not_split(self, marp_blocks):
        """Gotcha 3: `---` directly under a text line is a heading underline.
        Lines 22-24 stay on slide three."""
        assert [r["startLine"] for r in slide_specs(marp_blocks)[2]["rows"]] == [
            20,
            22,
            24,
        ]

    def test_a_document_without_breaks_is_a_single_slide(self, plain_blocks):
        slides = slide_specs(plain_blocks)
        assert len(slides) == 1
        assert [r["startLine"] for r in slides[0]["rows"]] == [1, 3, 5, 7]

    def test_no_blocks_no_slides(self):
        assert slide_specs([]) == []
        assert slide_specs(None) == []

    def test_fenced_code_containing_a_rule_does_not_split(self):
        """A `---` inside a fence is content, not a break."""
        blocks = render_markdown_blocks("# T\n\n```\n---\n```\n\nafter\n")
        assert len(slide_specs(blocks)) == 1

    def test_a_starred_rule_does_not_split(self):
        """Marp splits on `---`.  `***` renders a real horizontal rule and must
        stay visible on the slide rather than silently cutting the deck."""
        blocks = render_markdown_blocks("# T\n\n***\n\nafter\n")
        slides = slide_specs(blocks)
        assert len(slides) == 1
        assert "<hr>" in [r["html"] for r in slides[0]["rows"]]

    def test_adjacent_breaks_make_no_blank_slide(self):
        blocks = render_markdown_blocks("# One\n\n---\n\n---\n\n# Two\n")
        assert [[r["startLine"] for r in s["rows"]] for s in slide_specs(blocks)] == [
            [1],
            [7],
        ]

    def test_a_trailing_break_makes_no_blank_slide(self):
        blocks = render_markdown_blocks("# One\n\n---\n\n# Two\n\n---\n")
        assert [[r["startLine"] for r in s["rows"]] for s in slide_specs(blocks)] == [
            [1],
            [5],
        ]

    def test_a_deck_must_not_open_with_a_slide_break(self):
        """Known Marp/YAML trap, documented rather than silently wrong: a `---`
        on line 1 opens front matter, so it is swallowed as metadata together
        with everything up to the next `---`.  Start decks with content."""
        blocks = render_markdown_blocks("---\n\n# Only\n\n---\n")
        assert [b["type"] for b in blocks] == ["front_matter"]
        assert slide_specs(blocks) == []


class TestAnchorParity:
    """**The most important test in the PR.**

    A block must keep the identical ``token.map`` line range — and therefore the
    identical comment anchor — in review mode and in presentation mode.
    """

    COMMENTS = {7: [{"id": 1, "body": "on the title"}], 15: [{"id": 2}, {"id": 3}]}

    def test_every_slide_row_is_the_review_row_verbatim(self, marp_blocks):
        review = {r["id"]: r for r in source_row_specs(marp_blocks, self.COMMENTS)}
        for row in _rows(slide_specs(marp_blocks, self.COMMENTS)):
            assert row == review[row["id"]], (
                f"block {row['id']} renders differently in presentation mode"
            )

    def test_line_ranges_are_identical_in_both_modes(self, marp_blocks):
        review = source_row_specs(marp_blocks, self.COMMENTS)
        presented = _rows(slide_specs(marp_blocks, self.COMMENTS))
        review_ranges = {(r["startLine"], r["endLine"]) for r in review}
        presented_ranges = {(r["startLine"], r["endLine"]) for r in presented}
        # Presentation mode drops only the delimiters and metadata; every range
        # it *does* show is byte-identical to the review-mode one.
        assert presented_ranges <= review_ranges
        assert review_ranges - presented_ranges == {(1, 5), (11, 11), (18, 18)}

    def test_a_comment_resolves_to_the_same_block_in_both_modes(self, marp_blocks):
        """A comment made in review mode must still belong to that block after a
        mode flip — the contract the 2026-07-22 comment-loss incident broke."""
        review = {r["id"]: r for r in source_row_specs(marp_blocks, self.COMMENTS)}
        presented = {r["id"]: r for r in _rows(slide_specs(marp_blocks, self.COMMENTS))}

        for anchor, count in (("L7", 1), ("L15", 2)):
            assert presented[anchor]["commentCount"] == count
            assert presented[anchor]["commentCount"] == review[anchor]["commentCount"]
            assert presented[anchor]["startLine"] == review[anchor]["startLine"]
            assert presented[anchor]["endLine"] == review[anchor]["endLine"]

    def test_anchor_ids_are_still_the_block_start_line(self, marp_blocks):
        for row in _rows(slide_specs(marp_blocks, self.COMMENTS)):
            assert row["id"] == f"L{row['startLine']}"

    def test_comment_markers_survive_json_string_keys(self, marp_blocks):
        """The client gets `comments_by_block` back from JSON with str keys."""
        as_str = {str(k): v for k, v in self.COMMENTS.items()}
        assert slide_specs(marp_blocks, as_str) == slide_specs(
            marp_blocks, self.COMMENTS
        )


class TestAvailabilityIsMetadataGated:
    """Availability requires an explicit ``marp: true`` declaration (#455).

    #452 also offered presentation mode to anything already split into slides
    (``len(slides) > 1``).  That is the half being dropped: presenting
    arbitrary markdown makes no sense, and any prose document that happens to
    contain two ``---`` rules is not a deck.  **This is a behavioural change** —
    a file relying on the implicit multi-slide path loses its Present button.
    """

    def test_a_declared_deck_offers_presentation_mode(self, marp_blocks):
        assert presentation_specs(marp_blocks, None, MARP_DOC)["available"] is True

    def test_multi_slide_document_without_front_matter_does_not(self):
        """The dropped fallback.  The document still *groups* into slides — it
        simply is not offered, because nothing declared it a presentation."""
        source = "# One\n\n---\n\n# Two\n"
        blocks = render_markdown_blocks(source)
        specs = presentation_specs(blocks, None, source)
        assert len(specs["slides"]) == 2
        assert specs["available"] is False

    def test_ordinary_document_does_not_offer_presentation_mode(self, plain_blocks):
        assert presentation_specs(plain_blocks, None, PLAIN_DOC)["available"] is False

    def test_front_matter_without_a_marp_directive_does_not(self):
        """Metadata alone is not a declaration; the directive is."""
        source = "---\ntitle: Notes\n---\n\n# One\n\n---\n\n# Two\n"
        blocks = render_markdown_blocks(source)
        assert presentation_specs(blocks, None, source)["available"] is False

    def test_marp_false_does_not(self):
        source = "---\nmarp: false\n---\n\n# One\n\n---\n\n# Two\n"
        blocks = render_markdown_blocks(source)
        assert presentation_specs(blocks, None, source)["available"] is False

    def test_the_declaration_is_case_insensitive(self):
        source = "---\nmarp: True\n---\n\n# One\n"
        blocks = render_markdown_blocks(source)
        assert presentation_specs(blocks, None, source)["available"] is True

    def test_a_declared_single_slide_deck_is_still_offered(self):
        """The author said it is a deck; a one-slide deck is their call."""
        source = "---\nmarp: true\n---\n\n# Only one\n"
        blocks = render_markdown_blocks(source)
        specs = presentation_specs(blocks, None, source)
        assert len(specs["slides"]) == 1
        assert specs["available"] is True

    def test_a_declared_deck_with_no_slides_is_not_offered(self):
        """A declaration is necessary, not sufficient: a file whose front
        matter swallows the whole document has nothing to present, and the
        Present button would be dead (``enterPresentation`` bails on an empty
        deck)."""
        source = "---\nmarp: true\n---\n"
        specs = presentation_specs(render_markdown_blocks(source), None, source)
        assert specs["slides"] == []
        assert specs["available"] is False


class TestPresentationSpecs:
    def test_theme_comes_from_the_front_matter(self, marp_blocks):
        assert presentation_specs(marp_blocks, None, MARP_DOC)["theme"] == "default"

    def test_theme_falls_back_when_undeclared(self, plain_blocks):
        assert presentation_specs(plain_blocks, None, PLAIN_DOC)["theme"] == "default"

    def test_theme_is_constrained_to_a_known_set(self):
        """The theme lands in a CSS class name; an arbitrary front-matter string
        must not get there."""
        source = "---\nmarp: true\ntheme: ../../evil \"x\n---\n\n# T\n"
        blocks = render_markdown_blocks(source)
        assert presentation_specs(blocks, None, source)["theme"] == "default"

    def test_paginate_is_a_boolean(self, marp_blocks):
        assert presentation_specs(marp_blocks, None, MARP_DOC)["paginate"] is True

    def test_paginate_defaults_off(self, plain_blocks):
        assert presentation_specs(plain_blocks, None, PLAIN_DOC)["paginate"] is False

    def test_slides_match_the_slide_builder(self, marp_blocks):
        comments = {7: [{"id": 1}]}
        assert presentation_specs(marp_blocks, comments, MARP_DOC)[
            "slides"
        ] == slide_specs(marp_blocks, comments)


class TestReviewModeIsUnchanged:
    """Requirement 4: a document with no `---` and no front matter must render
    exactly as it does today."""

    def test_plain_document_blocks_are_untouched(self, plain_blocks):
        assert [(b["start_line"], b["end_line"]) for b in plain_blocks] == [
            (1, 1),
            (3, 3),
            (5, 5),
            (7, 7),
        ]
        assert [b["html"] for b in plain_blocks] == [
            "<h1>Title</h1>",
            "<p>Just a paragraph.</p>",
            "<h2>Section</h2>",
            "<p>More text.</p>",
        ]

    def test_plain_document_row_specs_are_untouched(self, plain_blocks):
        assert source_row_specs(plain_blocks, None)[0] == {
            "id": "L1",
            "rowClass": "source-line",
            "startLine": 1,
            "endLine": 1,
            "label": "1",
            "html": "<h1>Title</h1>",
            "commentCount": 0,
        }

    def test_a_lone_thematic_break_still_renders_in_review_mode(self):
        """Review mode shows every block, breaks included — only presentation
        mode treats them as delimiters."""
        blocks = render_markdown_blocks("# T\n\n---\n\n# U\n")
        assert [b["html"] for b in blocks] == ["<h1>T</h1>", "<hr>", "<h1>U</h1>"]


class TestPyodideBridgeContract:
    """The client calls these builders across the WASM bridge as
    ``json.dumps(fn(*json.loads(arg)))``.  Exercise that exact marshalling: JSON
    stringifies int dict keys and turns tuples into lists, so a builder that
    only works on native Python input would break only in the browser."""

    @staticmethod
    def _via_bridge(fn, args):
        import json

        return json.loads(json.dumps(fn(*json.loads(json.dumps(args)))))

    def test_slide_specs_survive_the_round_trip(self, marp_blocks):
        comments = {7: [{"id": 1}]}
        assert self._via_bridge(slide_specs, [marp_blocks, comments]) == slide_specs(
            marp_blocks, comments
        )

    def test_slide_specs_accept_the_js_null_defaults(self):
        assert self._via_bridge(slide_specs, [None, None]) == []

    def test_presentation_specs_survive_the_round_trip(self, marp_blocks):
        assert self._via_bridge(
            presentation_specs, [marp_blocks, None, MARP_DOC]
        ) == presentation_specs(marp_blocks, None, MARP_DOC)

    def test_layouts_survive_the_round_trip(self, layout_blocks):
        assert [
            slide["layout"]
            for slide in self._via_bridge(slide_specs, [layout_blocks, None])
        ] == EXPECTED_LAYOUTS


# ── Per-slide layouts via Marp `_class` directives (#462) ──
#
# One deck covering the whole scoping matrix.  Marp has two forms and they
# scope differently: `_class` is a *spot* directive (this slide only) and
# `class` is global (this slide and every one after it, until overridden).
# Getting that backwards leaks a title layout across an entire deck.
LAYOUT_DOC = "\n".join(
    [
        "---",                              # 1
        "marp: true",                       # 2
        "---",                              # 3
        "",                                 # 4
        "<!-- _class: title -->",           # 5
        "",                                 # 6
        "# Slide one",                      # 7
        "",                                 # 8
        "---",                              # 9
        "",                                 # 10
        "# Slide two",                      # 11
        "",                                 # 12
        "---",                              # 13
        "",                                 # 14
        "<!-- class: quote -->",            # 15
        "",                                 # 16
        "# Slide three",                    # 17
        "",                                 # 18
        "---",                              # 19
        "",                                 # 20
        "# Slide four",                     # 21
        "",                                 # 22
        "---",                              # 23
        "",                                 # 24
        "<!-- _class: centered -->",        # 25
        "",                                 # 26
        "# Slide five",                     # 27
        "",                                 # 28
        "---",                              # 29
        "",                                 # 30
        "# Slide six",                      # 31
        "",                                 # 32
        "---",                              # 33
        "",                                 # 34
        "<!-- _class: nonesuch -->",        # 35
        "",                                 # 36
        "# Slide seven",                    # 37
        "",                                 # 38
        "---",                              # 39
        "",                                 # 40
        "<!-- TODO: an ordinary comment -->",  # 41
        "",                                 # 42
        "# Slide eight",                    # 43
        "",                                 # 44
        "---",                              # 45
        "",                                 # 46
        "<!-- class: title",                # 47
        "_paginate: false -->",             # 48
        "",                                 # 49
        "# Slide nine",                     # 50
        "",                                 # 51
    ]
)

EXPECTED_LAYOUTS = [
    "title",     # 1: spot directive
    "default",   # 2: the spot directive did not leak forward
    "quote",     # 3: persistent directive takes effect on its own slide
    "quote",     # 4: …and carries forward
    "centered",  # 5: a spot directive outranks the persistent one
    "quote",     # 6: …without cancelling it
    "default",   # 7: unknown name is rejected, not passed through
    "quote",     # 8: an ordinary HTML comment is not a directive
    "title",     # 9: a later persistent directive replaces the earlier one
]


@pytest.fixture
def layout_blocks():
    return render_markdown_blocks(LAYOUT_DOC)


class TestCommentDirectives:
    """Reading directives out of a block.

    The parser runs with ``html: False``, so an HTML comment is **not**
    invisible here: it arrives as a ``paragraph`` carrying the comment text.
    Before #462 a real Marp deck therefore rendered its own directives as
    visible body text.  The directives have to be read from ``raw`` and the
    block dropped, or the fix is only half done.

    ``renderer.py`` now blanks the *html* of any comment-only block, so the
    escaped text no longer shows in review mode either.  That is a second,
    independent suppression: it hides every comment, this one drops directive
    blocks off the slide.  Neither substitutes for the other, and both still
    read the same ``raw``.
    """

    @staticmethod
    def _block(source):
        blocks = render_markdown_blocks(source)
        assert len(blocks) == 1, blocks
        return blocks[0]

    def test_a_directive_comment_is_not_invisible_to_the_parser(self):
        """The premise of the whole change — if this ever fails, comments have
        become real HTML and the suppression below is dead code.

        Read off ``raw``, not ``html``.  The renderer blanks the html of a
        comment-only block, so an empty ``html`` no longer says anything about
        how the parser typed it, while ``raw`` is what this module reads and
        ``paragraph`` is what tells a directive from a code sample."""
        block = self._block("<!-- _class: title -->\n")
        assert block["type"] == "paragraph"
        assert "_class" in block["raw"]

    def test_a_spot_directive_is_read_from_raw(self):
        assert comment_directives(self._block("<!-- _class: title -->\n")) == {
            "_class": "title"
        }

    def test_a_persistent_directive_is_read_from_raw(self):
        assert comment_directives(self._block("<!-- class: quote -->\n")) == {
            "class": "quote"
        }

    def test_a_multi_line_directive_comment_is_read_whole(self):
        assert comment_directives(
            self._block("<!-- class: quote\n_paginate: false -->\n")
        ) == {"class": "quote", "_paginate": "false"}

    def test_a_run_of_adjacent_directive_comments_is_read_whole(self):
        """MS-608, the shape a real Marp deck writes.  Two directives on
        consecutive lines are one paragraph block, because markdown-it
        continues a paragraph lazily, so both must be read out of the one
        ``raw``."""
        assert comment_directives(
            self._block("<!-- _class: quote -->\n<!-- paginate: true -->\n")
        ) == {"_class": "quote", "paginate": "true"}

    def test_a_later_comment_in_a_run_can_carry_the_directive(self):
        """The first comment is not privileged.  Reading only it would leave
        the block on the slide, showing both comments as escaped text."""
        assert comment_directives(
            self._block("<!-- paginate: true -->\n<!-- _class: quote -->\n")
        ) == {"paginate": "true", "_class": "quote"}

    def test_prose_inside_one_comment_of_a_run_is_not_a_directive(self):
        """One non-directive line disqualifies the whole block, as it does
        inside a single multi-line comment — the prose would leave with it."""
        assert (
            comment_directives(
                self._block("<!-- _class: title -->\n<!-- and some prose -->\n")
            )
            == {}
        )

    def test_an_ordinary_comment_is_not_a_directive(self):
        """Decided (issue constraint 4): only comments naming a directive we
        act on are swallowed.  Treating every comment as a directive would
        silently delete content on the strength of a guess."""
        assert comment_directives(self._block("<!-- TODO: later -->\n")) == {}

    def test_prose_is_not_a_directive(self):
        assert comment_directives(self._block("just a paragraph\n")) == {}

    def test_a_comment_with_trailing_prose_is_not_a_directive(self):
        """Half a directive block is not a directive block; dropping it would
        take the prose with it."""
        assert comment_directives(
            self._block("<!-- _class: title --> and some text\n")
        ) == {}

    def test_a_comment_with_a_non_directive_line_is_not_a_directive(self):
        """Same rule, the multi-line spelling — the riskier one, because the
        prose is *inside* the comment and would leave with it."""
        assert comment_directives(
            self._block("<!-- _class: title\nand some prose -->\n")
        ) == {}

    def test_an_indented_code_block_is_not_a_directive(self):
        """Structural, like ``_is_slide_break``: a directive *shown as an
        example* is content.  Indented code keeps its indentation in ``raw``
        but strips to a bare comment, so text alone cannot tell the two
        apart — and swallowing it deletes a code sample from the slide."""
        block = self._block("    <!-- _class: title -->\n")
        assert block["type"] == "code_block"
        assert comment_directives(block) == {}

    def test_a_fenced_code_block_is_not_a_directive(self):
        block = self._block("```\n<!-- _class: title -->\n```\n")
        assert block["type"] == "fence"
        assert comment_directives(block) == {}

    def test_two_comments_around_prose_are_not_a_directive(self):
        """MS-607.  ``<!-- a --> prose <!-- b -->`` starts with ``<!--`` and
        ends with ``-->`` while being two comments, so a bare ends-with test
        read it as one directive block and ``slide_specs`` dropped the prose
        off the deck.  Review mode always rendered the same block, so the two
        modes disagreed about whether the text existed."""
        assert comment_directives(
            self._block("<!-- _class: title --> MID PROSE <!-- x: y -->\n")
        ) == {}

    def test_an_unterminated_comment_is_not_a_directive(self):
        """``<!-->`` satisfies both a startswith and an endswith test on
        overlapping characters, and is not a comment at all."""
        assert comment_directives(self._block("<!-->\n")) == {}

    @pytest.mark.parametrize(
        "raw",
        [
            "<!-- _class: title -->",
            "<!---->",
            "<!-->",
            "<!--->",
            "<!-- _class: title --> MID PROSE <!-- x: y -->",
            "<!-- _class: title -->-->",
            "<!-- a\nb -->",
            "<!-- a -->\n",
            "not a comment",
            "<!-- unterminated",
            "<!-- a -->\n<!-- b -->",
            "<!-- a --> <!-- b -->",
            "<!-- a --><!-- b -->",
            "<!-- a -->\n<!-- b --> and some text",
            "<!-- a -->\n<!-- unterminated",
        ],
    )
    def test_the_all_comments_test_matches_the_renderer(self, raw):
        """The two modules cannot share the predicate — ``view_specs`` is
        loaded into Pyodide as a bare file and imports nothing — so this test
        is what ties them together.  They must agree about which blocks are
        wholly comment, or a block the renderer blanks keeps its prose while
        presentation mode drops it, or the reverse.

        The trailing-newline case earns its place: it is the one that tells
        ``\\Z`` from ``$``, which are otherwise interchangeable here."""
        assert _is_all_comments(raw) == bool(_COMMENTS_ONLY_RE.match(raw))


class TestLayoutScoping:
    def test_every_slide_carries_a_layout(self, layout_blocks):
        slides = slide_specs(layout_blocks)
        assert [slide["layout"] for slide in slides] == EXPECTED_LAYOUTS

    def test_a_deck_without_directives_is_all_default(self, marp_blocks):
        assert [s["layout"] for s in slide_specs(marp_blocks)] == [
            "default",
            "default",
            "default",
        ]

    def test_a_spot_directive_does_not_leak_to_the_next_slide(self):
        source = "---\nmarp: true\n---\n\n<!-- _class: title -->\n\n# A\n\n---\n\n# B\n"
        slides = slide_specs(render_markdown_blocks(source))
        assert [s["layout"] for s in slides] == ["title", "default"]

    def test_a_persistent_directive_applies_to_its_own_slide_too(self):
        source = "<!-- class: quote -->\n\n# A\n\n---\n\n# B\n"
        slides = slide_specs(render_markdown_blocks(source))
        assert [s["layout"] for s in slides] == ["quote", "quote"]

    def test_an_unknown_layout_falls_back_to_default(self):
        """The name lands in a CSS class, exactly like ``theme``; an arbitrary
        document string must never reach a class attribute."""
        source = '<!-- _class: ../../evil "x -->\n\n# A\n'
        slides = slide_specs(render_markdown_blocks(source))
        assert [s["layout"] for s in slides] == ["default"]

    def test_every_shipped_layout_is_reachable(self):
        """A whitelist entry with no way to select it is a dead layout."""
        for layout in PRESENTATION_LAYOUTS:
            source = "<!-- _class: %s -->\n\n# A\n" % layout
            slides = slide_specs(render_markdown_blocks(source))
            assert slides[0]["layout"] == layout

    def test_presentation_specs_carry_the_layouts(self):
        specs = presentation_specs(
            render_markdown_blocks(LAYOUT_DOC), None, LAYOUT_DOC
        )
        assert [s["layout"] for s in specs["slides"]] == EXPECTED_LAYOUTS


class TestDirectiveBlockSuppression:
    """A directive block is metadata, so it is dropped from the slide exactly
    as ``front_matter`` is — but *dropped*, never renumbered."""

    def test_a_directive_block_is_not_slide_content(self, layout_blocks):
        html = " ".join(row["html"] for row in _rows(slide_specs(layout_blocks)))
        assert "_class" not in html
        assert "class: quote" not in html

    def test_an_ordinary_comment_keeps_its_row(self, layout_blocks):
        """Constraint 4: an unrecognised comment is not swallowed on a guess.

        Its row survives onto the slide, and so does the comment anchor that
        row carries.  What it no longer does is *show*: the renderer blanks the
        html of every comment-only block, so this asserts the row is present
        and empty.  Dropping the row and blanking its html look the same on
        screen and are not the same thing — only one of them loses the
        anchor."""
        rows = {row["startLine"]: row for row in _rows(slide_specs(layout_blocks))}
        assert 41 in rows, "the ordinary comment at line 41 left the deck"
        assert rows[41]["html"] == ""

    def test_line_ranges_are_unchanged_by_suppression(self, layout_blocks):
        """The 2026-07-22 comment-loss guard, at the point of maximum risk:
        dropping a block must not shift the block after it."""
        kept = {
            (row["startLine"], row["endLine"])
            for row in _rows(slide_specs(layout_blocks))
        }
        review = {
            (row["startLine"], row["endLine"])
            for row in source_row_specs(layout_blocks)
        }
        assert kept <= review
        # Only the front matter, the eight breaks and the four directive blocks
        # are absent; every other block survives with its own range.
        assert review - kept == {
            (1, 3),    # front matter
            (5, 5),    # <!-- _class: title -->
            (9, 9),    # ---
            (13, 13),  # ---
            (15, 15),  # <!-- class: quote -->
            (19, 19),  # ---
            (23, 23),  # ---
            (25, 25),  # <!-- _class: centered -->
            (29, 29),  # ---
            (33, 33),  # ---
            (35, 35),  # <!-- _class: nonesuch -->
            (39, 39),  # ---
            (45, 45),  # ---
            (47, 48),  # <!-- class: title / _paginate: false -->
        }

    def test_rows_are_still_the_review_rows_verbatim(self, layout_blocks):
        review = {row["id"]: row for row in source_row_specs(layout_blocks)}
        for row in _rows(slide_specs(layout_blocks)):
            assert row == review[row["id"]]

    def test_a_code_sample_of_a_directive_stays_on_the_slide(self):
        """A deck that documents this very feature must keep its own example —
        and must not pick up the layout it is only demonstrating."""
        source = "text\n\n    <!-- _class: title -->\n\n# A\n"
        slides = slide_specs(render_markdown_blocks(source))
        assert [row["startLine"] for row in _rows(slides)] == [1, 3, 5]
        assert slides[0]["layout"] == "default"

    def test_a_slide_of_nothing_but_a_directive_is_not_a_slide(self):
        """``_append_slide`` already refuses blank slides; a suppressed
        directive must not resurrect one."""
        source = "# A\n\n---\n\n<!-- _class: title -->\n\n---\n\n# B\n"
        slides = slide_specs(render_markdown_blocks(source))
        assert len(slides) == 2
        assert [s["layout"] for s in slides] == ["default", "default"]

    def test_prose_between_two_comments_stays_on_the_slide(self):
        """MS-607, the loss this guards.  ``<!-- a --> prose <!-- b -->`` is one
        block that is not one comment, so it is slide content: it keeps its row,
        its line range and its comment anchor, and it takes no layout from the
        directive it appears to carry.

        The html stays escaped rather than blank, because the renderer suppresses
        a block that is *wholly* a comment and this one is not."""
        source = "# A\n\n<!-- _class: title --> MID PROSE <!-- x: y -->\n"
        slides = slide_specs(render_markdown_blocks(source))
        rows = {row["startLine"]: row for row in _rows(slides)}
        assert 3 in rows, "the prose block left the deck"
        assert "MID PROSE" in rows[3]["html"]
        assert slides[0]["layout"] == "default"

    def test_a_run_of_directive_comments_leaves_the_slide(self):
        """MS-608.  Two directives on consecutive lines are one block, and that
        block is wholly comment, so it is metadata: it takes its layout and
        drops off the deck rather than showing as escaped text."""
        source = "<!-- _class: quote -->\n<!-- paginate: true -->\n\n# A\n"
        slides = slide_specs(render_markdown_blocks(source))
        assert [row["startLine"] for row in _rows(slides)] == [4]
        assert slides[0]["layout"] == "quote"

    def test_suppression_does_not_change_review_mode(self, layout_blocks):
        """Review mode keeps a row for every block, directives included — this
        is a presentation-mode grouping decision, not a parse change.

        Asserted on the rows rather than on their text, because the renderer
        blanks a comment's html in both modes.  The distinction this guards is
        between a block *missing from review mode*, which would take its
        comment anchor with it, and a block that is present and renders
        nothing."""
        rows = {row["startLine"] for row in source_row_specs(layout_blocks)}
        assert {5, 15, 25, 35, 41, 47} <= rows


class TestMermaidReachesTheDeck:
    """MS-605: a mermaid fence arrives on a slide as un-rendered source.

    ``renderer.py`` emits escaped fence text inside ``<div class="mermaid">``
    and leaves the drawing to the browser.  Presentation mode reuses those very
    rows, so the deck inherits the container and must run its own render pass.
    These assertions pin the contract the JS half depends on; the rendering
    itself is covered by ``test_mermaid_init.js``.
    """

    MERMAID_DECK = "\n".join(
        [
            "---",
            "marp: true",
            "---",
            "",
            "# Slide one",
            "",
            "```mermaid",
            "graph TD",
            "    A-->B",
            "```",
            "",
            "---",
            "",
            "# Slide two",
            "",
            "```mermaid",
            "graph LR",
            "    C-->D",
            "```",
            "",
        ]
    )

    @pytest.fixture
    def specs(self):
        blocks = render_markdown_blocks(self.MERMAID_DECK)
        return presentation_specs(blocks, None, self.MERMAID_DECK)

    def test_the_deck_carries_the_container_not_a_diagram(self, specs):
        html = " ".join(row["html"] for row in _rows(specs["slides"]))
        assert '<div class="mermaid">' in html
        assert "<svg" not in html

    def test_each_fence_is_its_own_container(self, specs):
        html = " ".join(row["html"] for row in _rows(specs["slides"]))
        assert html.count('<div class="mermaid">') == 2

    def test_the_source_survives_the_mode_flip_verbatim(self, specs):
        """The deck's container must hold the same escaped source review mode
        shows, because the browser renders from its ``textContent``."""
        review = {
            row["id"]: row
            for row in source_row_specs(render_markdown_blocks(self.MERMAID_DECK))
        }
        deck_rows = [
            row for row in _rows(specs["slides"]) if "mermaid" in row["html"]
        ]
        assert len(deck_rows) == 2
        for row in deck_rows:
            assert row["html"] == review[row["id"]]["html"]
