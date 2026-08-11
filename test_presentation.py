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

from renderer import render_markdown_blocks
from view_specs import (
    PRESENTATION_LAYOUTS,
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
    invisible here: it arrives as a ``paragraph`` whose html is the *escaped*
    comment text.  Before #462 a real Marp deck therefore rendered its own
    directives as visible body text.  The directives have to be read from
    ``raw`` and the block dropped, or the fix is only half done.
    """

    @staticmethod
    def _block(source):
        blocks = render_markdown_blocks(source)
        assert len(blocks) == 1, blocks
        return blocks[0]

    def test_a_directive_comment_is_not_invisible_to_the_parser(self):
        """The premise of the whole change — if this ever fails, comments have
        become real HTML and the suppression below is dead code."""
        block = self._block("<!-- _class: title -->\n")
        assert block["type"] == "paragraph"
        assert "_class" in block["html"]

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

    def test_an_ordinary_comment_still_renders(self, layout_blocks):
        """Constraint 4: an unrecognised comment keeps today's behaviour rather
        than being silently swallowed."""
        html = " ".join(row["html"] for row in _rows(slide_specs(layout_blocks)))
        assert "TODO: an ordinary comment" in html

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

    def test_suppression_does_not_change_review_mode(self, layout_blocks):
        """Review mode shows every block, directives included — this is a
        presentation-mode grouping decision, not a parse change."""
        html = " ".join(row["html"] for row in source_row_specs(layout_blocks))
        assert "_class: title" in html
        assert "class: quote" in html
