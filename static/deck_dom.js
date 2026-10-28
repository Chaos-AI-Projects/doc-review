/* doc-review — presentation deck DOM construction (MS-619).
 *
 * The builder takes the `document` to create nodes with and the specs
 * presentation_specs() returns, so it is reachable from Node.  That is what
 * test_deck_dom.js exercises, and it is the same code the browser runs: app.js
 * consumes this as `window.docReviewDeck`.
 *
 * It exists as its own module because the deck now has two callers.  In the
 * review app it is a mode the Present button flips into, over a hidden review
 * DOM.  On ChaosEternal.github.io it is the whole page: no comments, no
 * navigator, no server.  A second hand-written deck builder for the static page
 * would drift from this one silently, and a slide that renders differently in
 * the two places is exactly the bug the shared view_specs.py was written to
 * prevent.
 *
 * Nothing here decides anything about the deck.  Theme, per-slide layouts,
 * slide grouping and pagination all arrive already settled in the specs, whose
 * layout-name whitelist view_specs applied (#462).  A JS-side default would be
 * a second, divergent home for that whitelist.  test_server.py counts the
 * reads across both JS files to keep it that way.
 */
(function (root) {
    "use strict";

    /* On-screen controls (#455).  A phone has no arrow keys and no Esc, so
     * every keyboard action needs a pointer equivalent.  These action strings
     * are the SAME vocabulary navLogic.presentationAction() produces for keys,
     * so both input routes can end in one dispatcher. */
    var PRESENTATION_CONTROLS = [
        { action: "prev", glyph: "\u2039", label: "Previous slide" },
        { action: "next", glyph: "\u203A", label: "Next slide" },
        { action: "exit", glyph: "\u2715", label: "Exit presentation" },
    ];

    /* Which of them a given caller gets, because the two callers can do
     * different things (MS-619).  The review app can exit, back into the
     * review DOM the deck covers.  The standalone page has nothing behind the
     * deck and cannot, and a button that does nothing is worse there than no
     * button: the control bar is the only evidence a phone viewer has that the
     * page is alive.
     *
     * The CALLER names its actions and this file keeps the glyph and the
     * label, so "next" cannot be a chevron on one page and an arrow on the
     * other.  An action with no definition here is dropped rather than guessed
     * at, the way nav_logic treats a key it does not know. */
    function controlsFor(actions) {
        if (!actions) return PRESENTATION_CONTROLS;
        return PRESENTATION_CONTROLS.filter(function (def) {
            return actions.indexOf(def.action) !== -1;
        });
    }

    /* app.js sets the same two attributes on its review rows and keeps its own
     * copy of this.  Deliberate: sharing it would make deck_dom.js a hard
     * dependency of the review render, where today a missing module costs only
     * the Present button.  The attribute name already has homes in view.html's
     * Jinja and in app.js's reader, so one more is not what would close that
     * hole, and nothing reads the anchor off a slide block — presentation mode
     * is read-only. */
    function setLineAttrs(el, spec) {
        el.setAttribute("data-line-start", spec.startLine);
        el.setAttribute("data-line-end", spec.endLine);
    }

    /* A control press is the pointer equivalent of a keypress, so it resolves
     * to an action string and is handed to the caller's dispatcher.  The press
     * stops here rather than bubbling: there is deliberately no whole-slide
     * click-to-advance, because a tap anywhere would make an overflowing slide
     * impossible to scroll and text impossible to select on a phone. */
    function buildControls(doc, onAction, actions) {
        var defs = controlsFor(actions);
        var bar = doc.createElement("div");
        bar.className = "presentation-controls";
        for (var i = 0; i < defs.length; i++) {
            var def = defs[i];
            var btn = doc.createElement("button");
            btn.type = "button";
            btn.className = "presentation-control control-" + def.action;
            btn.setAttribute("data-action", def.action);
            btn.setAttribute("aria-label", def.label);
            btn.title = def.label;
            btn.textContent = def.glyph;
            btn.addEventListener("click", function (e) {
                e.preventDefault();
                e.stopPropagation();
                // Read off the button rather than closing over `def`: one
                // handler body, and the action stays the attribute the DOM
                // actually carries.
                if (onAction) onAction(this.getAttribute("data-action"));
            });
            bar.appendChild(btn);
        }
        return bar;
    }

    /* Build the deck element for `specs`.  `onAction` receives the action
     * string of a pressed control and may be omitted, which renders a deck
     * whose controls do nothing — useful before navigation is wired.
     * `actions` names the controls to build; omitting it builds all of them. */
    function buildDeck(doc, specs, onAction, actions) {
        var deck = doc.createElement("div");
        deck.className = "presentation theme-" + specs.theme;
        deck.tabIndex = -1;  // focusable, so the keys reach the deck

        for (var i = 0; i < specs.slides.length; i++) {
            var slide = specs.slides[i];
            var section = doc.createElement("section");
            section.className = "slide layout-" + slide.layout;
            section.setAttribute("data-slide", slide.index);

            // A two-column slide arrives cut into regions (DR-11), each a
            // start/end slice of its rows; any other slide is one region
            // holding every row, mounted straight on the section.  Which
            // slides get regions is view_specs' decision, not this file's.
            var regions = slide.regions && slide.regions.length
                ? slide.regions
                : [{ name: null, start: 0, end: slide.rows.length }];
            for (var r = 0; r < regions.length; r++) {
                var region = regions[r];
                var parent = section;
                if (region.name) {
                    parent = doc.createElement("div");
                    parent.className = "slide-region region-" + region.name;
                    section.appendChild(parent);
                }
                for (var j = region.start; j < region.end; j++) {
                    var row = slide.rows[j];
                    var blockEl = doc.createElement("div");
                    blockEl.className = "slide-block";
                    // The review-mode anchor, carried onto the slide: same
                    // block, same line range, whichever mode you are in.
                    setLineAttrs(blockEl, row);
                    blockEl.innerHTML = row.html;
                    parent.appendChild(blockEl);
                }
            }

            if (specs.paginate) {
                var num = doc.createElement("div");
                num.className = "slide-number";
                num.textContent = slide.number + " / " + specs.slides.length;
                section.appendChild(num);
            }
            deck.appendChild(section);
        }
        // Part of the deck DOM, so they leave with it on exit — presenting
        // stays read-only and no stray chrome survives over the review view.
        deck.appendChild(buildControls(doc, onAction, actions));
        return deck;
    }

    /* The fixed stage (DR-2), after frontend-slides' viewport-base.css.  A
     * slide is laid out at 1920x1080 and scaled as a whole, so it looks the
     * same on a phone and a monitor instead of reflowing per device.  CSS
     * cannot divide one length by another to get a scale factor, so the
     * factor is computed here and handed to style.css as --stage-scale. */
    var STAGE_WIDTH = 1920;
    var STAGE_HEIGHT = 1080;

    var STAGE_PADDING_X = 128;  /* style.css: .presentation .slide padding */
    var CONTROL_GUTTER = 64;    /* window px the prev/next buttons cover */

    /* The largest scale at which the stage fits both axes; the other axis
     * letterboxes.  A window that reports no size has nothing to fit, and
     * scaling to 0 would make the deck vanish, so it stays at native size.
     *
     * The third bound keeps slide text clear of the prev/next controls.  Text
     * starts at the letterbox plus the scaled side padding, and on a phone that
     * sum falls under the 48px buttons.  Solving (w - 1920s)/2 + 128s >= 64
     * for s gives the bound, which only bites below 960px wide.  A deck too
     * narrow for any gutter (128px and under) drops the bound rather than
     * scaling to nothing or to a negative, mirrored slide. */
    function stageScale(width, height) {
        if (!(width > 0) || !(height > 0)) return 1;
        var fit = Math.min(width / STAGE_WIDTH, height / STAGE_HEIGHT);
        var gutter = (width - 2 * CONTROL_GUTTER) / (STAGE_WIDTH - 2 * STAGE_PADDING_X);
        return gutter > 0 ? Math.min(fit, gutter) : fit;
    }

    /* Fit `deck` to `win` now and on every resize.  Returns the detach
     * function: the review app enters and leaves the deck many times per
     * page, and a listener left behind per entry would pile up.
     *
     * The detach carries the fit itself as `.fit`.  A hidden deck measures
     * 0x0 and falls back to scale 1, so a page that hides the deck through a
     * resize has to refit it when it shows it again (DR-6). */
    function attachStageFit(deck, win) {
        /* Measured on the deck, not the window: under iOS pinch-zoom
         * innerWidth reports the zoomed visual viewport while the fixed deck
         * keeps its size, so the slide would rescale on every zoom. */
        function fit() {
            deck.style.setProperty("--stage-scale",
                String(stageScale(deck.clientWidth, deck.clientHeight)));
        }
        fit();
        win.addEventListener("resize", fit);
        var detach = function () { win.removeEventListener("resize", fit); };
        detach.fit = fit;
        return detach;
    }

    /* The fit check (DR-10).  A slide is a fixed 1920x1080 box with
     * `overflow: auto`, so content that does not fit used to get an inner
     * scrollbar and nothing else.  The rule is to split a crowded slide, never
     * to shrink it, so the check only reports.
     *
     * It measures the slide the browser actually laid out rather than
     * estimating from text length: fonts, images, tables and diagrams decide
     * the height, and only the browser knows them.  `transform: scale` leaves
     * scroll and client sizes alone, so the figures are stage pixels, the
     * same on a phone and a monitor.  One pixel of slack absorbs sub-pixel
     * rounding on a slide that fits exactly.
     *
     * The figure is only whole because style.css centres slides with `safe
     * center`.  Plain `center` spills an overflow above the top edge too,
     * where scrollHeight does not count it (test_theme_css.py guards this). */
    var FIT_SLACK = 1;

    function slideOverflow(section) {
        var over = Math.max(section.scrollHeight - section.clientHeight,
                            section.scrollWidth - section.clientWidth);
        return over > FIT_SLACK ? over : 0;
    }

    /* Judge one slide and set its flag to match, returning the overflow in
     * stage pixels.  Idempotent, and a slide that now fits loses its flag,
     * because a re-check is how a late image or diagram gets judged on its
     * final size.  The flag is absolutely positioned inside the slide, so it
     * adds nothing to the height it reports on.
     *
     * A slide that is not laid out (hidden, or inside a hidden deck) measures
     * 0x0, which would read as "fits".  It returns null and keeps its flag. */
    function flagSlide(doc, section) {
        if (!(section.clientWidth > 0) || !(section.clientHeight > 0)) return null;
        var over = slideOverflow(section);
        var flag = section.querySelector(".fit-flag");
        var classes = section.className.split(/\s+/).filter(function (c) {
            return c && c !== "overflowing";
        });
        if (over) classes.push("overflowing");
        section.className = classes.join(" ");
        if (!over) {
            if (flag) section.removeChild(flag);
            return 0;
        }
        if (!flag) {
            flag = doc.createElement("div");
            flag.className = "fit-flag";
            flag.setAttribute("role", "status");
            section.appendChild(flag);
        }
        flag.textContent = "Overflows the slide by " + over + "px -- split it";
        return over;
    }

    /* Judge every slide in `deck` and return the numbers (1-based) of those
     * that overflow.  Slides off screen are `hidden`, which lays them out at
     * 0x0, so each is shown for its measurement and put back as it was.
     * A deck that is itself hidden measures nothing, so the answer is null and
     * no flag changes: clearing them on a non-measurement would report a
     * crowded deck as clean. */
    function checkFit(doc, deck) {
        if (!(deck.clientWidth > 0) || !(deck.clientHeight > 0)) return null;
        var sections = deck.querySelectorAll("section.slide");
        var over = [];
        for (var i = 0; i < sections.length; i++) {
            var section = sections[i];
            var wasHidden = section.hidden;
            section.hidden = false;
            if (flagSlide(doc, section)) over.push(i + 1);
            section.hidden = wasHidden;
        }
        return over;
    }

    /* Re-judge slides whose size changes after the deck-wide check: an image
     * that loads on a slide, and web fonts that swap in for the fallback.
     * `load` does not bubble, so the listener captures.  A slide off screen
     * is skipped by flagSlide and judged again when it is shown. */
    function watchFit(doc, deck) {
        deck.addEventListener("load", function (e) {
            var slide = e.target && e.target.closest ? e.target.closest("section.slide") : null;
            if (slide) flagSlide(doc, slide);
        }, true);
        if (doc.fonts && doc.fonts.ready && typeof doc.fonts.ready.then === "function") {
            doc.fonts.ready.then(function () { checkFit(doc, deck); });
        }
    }

    var api = {
        PRESENTATION_CONTROLS: PRESENTATION_CONTROLS,
        attachStageFit: attachStageFit,
        stageScale: stageScale,
        buildControls: buildControls,
        buildDeck: buildDeck,
        checkFit: checkFit,
        flagSlide: flagSlide,
        slideOverflow: slideOverflow,
        watchFit: watchFit,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewDeck = api;
    }
})(typeof window !== "undefined" ? window : this);
