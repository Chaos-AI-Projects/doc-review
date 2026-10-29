#!/usr/bin/env node
/**
 * Behavioral test for deck DOM construction (MS-619).
 *
 * Loads the *real* module the browser runs (static/deck_dom.js) rather than a
 * mirrored copy, following test_mermaid_init.js and test_spa_nav.js.  The
 * module takes the `document` to build with, so the deck can be exercised here
 * without a browser -- and so the standalone Pages build and the review app can
 * share one builder instead of growing a second, divergent copy.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

var path = require("path");
var deckDom = require(path.join(__dirname, "static", "deck_dom.js"));

var failures = [];

function assert(label, actual, expected) {
    if (actual !== expected) {
        failures.push(label + ": expected " + JSON.stringify(expected) +
            ", got " + JSON.stringify(actual));
    }
}

function assertDeep(label, actual, expected) {
    var a = JSON.stringify(actual);
    var e = JSON.stringify(expected);
    if (a !== e) failures.push(label + ":\n      expected " + e + "\n      got      " + a);
}

/* ── A document stub deep enough for the builder ──
 *
 * querySelectorAll walks descendants and matches "tag", ".class" or
 * "tag.class", the same shape test_mermaid_init.js uses.
 */

function node(tag) {
    return {
        tag: tag,
        className: "",
        textContent: "",
        innerHTML: null,
        type: "",
        title: "",
        tabIndex: undefined,
        children: [],
        attrs: {},
        listeners: {},
        appendChild: function (child) { this.children.push(child); return child; },
        setAttribute: function (name, value) { this.attrs[name] = String(value); },
        getAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(this.attrs, name)
                ? this.attrs[name] : null;
        },
        addEventListener: function (type, fn) {
            (this.listeners[type] = this.listeners[type] || []).push(fn);
        },
        click: function () {
            var handlers = this.listeners.click || [];
            var evt = { defaultPrevented: false, propagationStopped: false,
                preventDefault: function () { this.defaultPrevented = true; },
                stopPropagation: function () { this.propagationStopped = true; } };
            for (var i = 0; i < handlers.length; i++) handlers[i].call(this, evt);
            return evt;
        },
        querySelectorAll: function (sel) { return queryAll(this, sel); },
        querySelector: function (sel) { return queryAll(this, sel)[0] || null; },
    };
}

var fakeDocument = { createElement: function (tag) { return node(tag); } };

function matches(n, sel) {
    var dot = sel.indexOf(".");
    var tag = dot === -1 ? sel : sel.slice(0, dot);
    var cls = dot === -1 ? null : sel.slice(dot + 1);
    if (tag && n.tag !== tag) return false;
    if (cls && n.className.split(/\s+/).indexOf(cls) === -1) return false;
    return true;
}

function queryAll(root, sel) {
    var out = [];
    (function walk(n) {
        for (var i = 0; i < n.children.length; i++) {
            if (matches(n.children[i], sel)) out.push(n.children[i]);
            walk(n.children[i]);
        }
    })(root);
    return out;
}

/* ── Fixtures ──
 *
 * The shape presentation_specs() returns (view_specs.py): a theme, a paginate
 * flag, and slides carrying the same row specs review mode renders, each with
 * its own line range.
 */

function row(html, startLine, endLine) {
    return { html: html, startLine: startLine, endLine: endLine };
}

function specs(overrides) {
    var base = {
        available: true,
        theme: "default",
        paginate: true,
        slides: [
            { index: 0, number: 1, layout: "title", rows: [row("<h1>Talk</h1>", 1, 1)] },
            { index: 1, number: 2, layout: "default",
              rows: [row("<h2>Two</h2>", 3, 3), row("<p>body</p>", 5, 6)] },
        ],
    };
    for (var k in (overrides || {})) base[k] = overrides[k];
    return base;
}

/* ── Cases ── */

// 1. The deck root carries the theme and is focusable, so keys reach it.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs({ theme: "gaia" }));
    assert("deck: root class", deck.className, "presentation theme-gaia");
    assert("deck: root is focusable", deck.tabIndex, -1);
})();

// 2. One section per slide, carrying the whitelisted layout name from
//    view_specs -- nothing here inspects or defaults it.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs());
    var sections = deck.querySelectorAll("section.slide");
    assert("slides: one section per slide", sections.length, 2);
    assert("slides: first layout", sections[0].className, "slide layout-title");
    assert("slides: second layout", sections[1].className, "slide layout-default");
    assert("slides: data-slide index", sections[1].getAttribute("data-slide"), "1");
})();

// 3. Each row becomes a block that keeps its review-mode line anchor.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs());
    var blocks = deck.querySelectorAll("div.slide-block");
    assert("blocks: one per row", blocks.length, 3);
    assert("blocks: html carried through", blocks[0].innerHTML, "<h1>Talk</h1>");
    assertDeep("blocks: line range carried through",
        [blocks[2].getAttribute("data-line-start"),
         blocks[2].getAttribute("data-line-end")], ["5", "6"]);
})();

// 4. Pagination is the spec's decision, not the builder's.
(function () {
    var paged = deckDom.buildDeck(fakeDocument, specs());
    var numbers = paged.querySelectorAll("div.slide-number");
    assert("paginate on: one number per slide", numbers.length, 2);
    assert("paginate on: number text", numbers[1].textContent, "2 / 2");

    var bare = deckDom.buildDeck(fakeDocument, specs({ paginate: false }));
    assert("paginate off: no numbers",
        bare.querySelectorAll("div.slide-number").length, 0);
})();

// 5. The controls are part of the deck, so they leave with it on exit.  A
//    phone has no arrow keys and no Esc, so every keyboard action needs a
//    pointer equivalent carrying the SAME action vocabulary nav_logic emits.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs());
    var bar = deck.querySelector("div.presentation-controls");
    assert("controls: bar exists", !!bar, true);
    // Everything below dereferences it, so a missing bar has to stop here or
    // the run dies on a TypeError stack instead of a named assertion.
    if (!bar) return;
    assert("controls: bar is last, after the slides",
        deck.children[deck.children.length - 1], bar);
    var actions = bar.querySelectorAll("button").map(function (b) {
        return b.getAttribute("data-action");
    });
    assertDeep("controls: action vocabulary", actions, ["prev", "next", "exit"]);
    assert("controls: labelled for screen readers",
        bar.querySelectorAll("button")[2].getAttribute("aria-label"),
        "Exit presentation");
})();

// 5b. A caller that cannot carry out an action does not get a button for it.
//     The standalone page has nothing behind the deck to exit to, and a dead
//     control on a phone reads as a page that has stopped responding.  The
//     caller names the actions; the glyph and the label stay here, so the two
//     pages cannot label the same action differently.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs(), null, ["next", "prev"]);
    var actions = deck.querySelector("div.presentation-controls")
        .querySelectorAll("button").map(function (b) {
            return b.getAttribute("data-action");
        });
    assertDeep("subset: only the named actions are built, in the builder's order",
        actions, ["prev", "next"]);
    var unknown = deckDom.buildDeck(fakeDocument, specs(), null, ["next", "zoom"]);
    assertDeep("subset: an action with no definition here is dropped",
        unknown.querySelector("div.presentation-controls")
            .querySelectorAll("button").map(function (b) {
                return b.getAttribute("data-action");
            }),
        ["next"]);
})();

// 6. A control press dispatches its action and goes no further.  There is
//    deliberately no whole-slide click-to-advance (#455), so the press must
//    not bubble into one.
(function () {
    var seen = [];
    var deck = deckDom.buildDeck(fakeDocument, specs(), function (action) {
        seen.push(action);
    });
    var buttons = deck.querySelector("div.presentation-controls")
        .querySelectorAll("button");
    var evt = buttons[1].click();
    assertDeep("press: dispatches its own action", seen, ["next"]);
    assert("press: default prevented", evt.defaultPrevented, true);
    assert("press: does not bubble", evt.propagationStopped, true);
})();

// 7. No handler is a no-op, not a crash: the builder must be usable to render
//    a deck before its navigation is wired.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs());
    var ok = true;
    try {
        deck.querySelector("div.presentation-controls")
            .querySelectorAll("button")[0].click();
    } catch (err) {
        ok = false;
        failures.push("no handler: threw " + err.message);
    }
    assert("no handler: press is a no-op", ok, true);
})();

// 8. The browser entry point, executed rather than read.  require() takes the
//    module.exports branch, so the `root.docReviewDeck` line runs in no other
//    case here, and test_server.py can only read it as text.  Swap the two
//    branches and every one of those checks still passes while a browser gets
//    nothing: window.docReviewDeck is undefined, the Present gate in app.js
//    never opens, and the button disappears without throwing.
(function () {
    var vm = require("vm");
    var fs = require("fs");
    var src = fs.readFileSync(
        path.join(__dirname, "static", "deck_dom.js"), "utf8");
    var win = {};
    vm.runInContext(src, vm.createContext({ window: win }));
    var api = win.docReviewDeck || {};
    assert("browser: publishes buildDeck", typeof api.buildDeck, "function");
    //  Identity against the require()d copy is the wrong check -- two loads
    //  give two function objects -- so exercise the published one instead.
    var deck = api.buildDeck ? api.buildDeck(fakeDocument, specs()) : null;
    assert("browser: the published builder builds",
        deck && deck.querySelectorAll("section.slide").length, 2);
})();

// 9. The fixed stage (DR-2): slides are laid out at 1920x1080 and scaled as
//    a whole, so a slide looks the same on a phone and a monitor.  The scale
//    is the largest that fits both axes; the other axis letterboxes.
(function () {
    assert("stage: native size", deckDom.stageScale(1920, 1080), 1);
    assert("stage: half size", deckDom.stageScale(960, 540), 0.5);
    assert("stage: wide window letterboxes", deckDom.stageScale(1920, 540), 0.5);
    // Below ~1000px wide the stage's own 128px side padding scales smaller
    // than the 48px prev/next controls, which then cover slide text.  The
    // stage shrinks just enough to keep 64px of window clear on each side.
    assert("stage: tall phone keeps a control gutter", deckDom.stageScale(390, 844), (390 - 128) / 1664);
    assert("stage: gutter leaves wide windows alone", deckDom.stageScale(1280, 720), 1280 / 1920);
    // Too narrow for any gutter: fit the window rather than vanish or mirror.
    assert("stage: no gutter at 128px", deckDom.stageScale(128, 500), 128 / 1920);
    assert("stage: no gutter at 100px", deckDom.stageScale(100, 500), 100 / 1920);
    (function () {
        var w = 600, s = deckDom.stageScale(w, 2000);
        var clear = (w - 1920 * s) / 2 + 128 * s;
        assert("stage: text clears the controls at 600px", clear >= 64 - 1e-9, true);
    })();
    // A window that reports no size (a hidden tab or iframe) must not scale
    // the deck to nothing: there is nothing to fit, so stay at native size.
    assert("stage: zero window", deckDom.stageScale(0, 0), 1);
    assert("stage: missing window size", deckDom.stageScale(undefined, 600), 1);
})();

// 10. attachStageFit fits at once, refits on every resize, and its detach
//     really removes the listener -- the review app enters and exits the deck
//     many times per page, and a leaked listener per entry would pile up.
(function () {
    var props = {};
    // Sized from the deck, not the window: under iOS pinch-zoom innerWidth
    // reports the zoomed visual viewport while the fixed deck keeps its size.
    var deck = { clientWidth: 960, clientHeight: 540,
                 style: { setProperty: function (k, v) { props[k] = v; } } };
    var win = {
        innerWidth: 9999, innerHeight: 9999, listeners: {},
        addEventListener: function (t, fn) {
            (this.listeners[t] = this.listeners[t] || []).push(fn);
        },
        removeEventListener: function (t, fn) {
            this.listeners[t] = (this.listeners[t] || []).filter(function (f) {
                return f !== fn;
            });
        },
        fire: function (t) {
            (this.listeners[t] || []).slice().forEach(function (f) { f(); });
        },
    };
    var detach = deckDom.attachStageFit(deck, win);
    assert("fit: immediate", props["--stage-scale"], "0.5");
    deck.clientWidth = 1920; deck.clientHeight = 270;
    win.fire("resize");
    assert("fit: refits on resize", props["--stage-scale"], "0.25");
    detach();
    assert("fit: detach removes the listener", (win.listeners.resize || []).length, 0);
    deck.clientWidth = 1920; deck.clientHeight = 1080;
    win.fire("resize");
    assert("fit: no refit after detach", props["--stage-scale"], "0.25");
    // A deck hidden through a resize measured 0x0 and fell back to scale 1,
    // so whoever shows it again has to be able to refit it on the spot (DR-6).
    deck.clientWidth = 960; deck.clientHeight = 540;
    assert("fit: the detach exposes a refit", typeof detach.fit, "function");
    detach.fit();
    assert("fit: the refit re-measures the deck", props["--stage-scale"], "0.5");
})();

// 11. The fit check (DR-10) measures each RENDERED slide against its own
//     box, so its stub models layout: a hidden slide measures 0x0, the way
//     `display: none` does in a browser.
function laidOut(content, box) {
    var s = node("section");
    s.className = "slide layout-default";
    s.hidden = true;
    box = box || { w: 1920, h: 1080 };
    function size(v) { return function () { return s.hidden ? 0 : v; }; }
    Object.defineProperty(s, "clientWidth", { get: size(box.w) });
    Object.defineProperty(s, "clientHeight", { get: size(box.h) });
    Object.defineProperty(s, "scrollWidth", { get: function () { return s.hidden ? 0 : content.w; } });
    Object.defineProperty(s, "scrollHeight", { get: function () { return s.hidden ? 0 : content.h; } });
    s.removeChild = function (c) {
        this.children = this.children.filter(function (x) { return x !== c; });
        return c;
    };
    return s;
}

function stubDeck(slides) {
    var d = node("div");
    d.className = "presentation theme-default";
    d.clientWidth = 1920;
    d.clientHeight = 1080;
    slides.forEach(function (s) { d.appendChild(s); });
    return d;
}

(function () {
    var fits = { w: 1920, h: 1080 };
    var tall = { w: 1920, h: 1320 };
    var wide = { w: 2100, h: 1080 };

    var shown = laidOut(fits);
    shown.hidden = false;
    assert("fit check: a slide that fits", deckDom.slideOverflow(shown), 0);
    var t = laidOut(tall); t.hidden = false;
    assert("fit check: vertical overflow in stage px", deckDom.slideOverflow(t), 240);
    var w = laidOut(wide); w.hidden = false;
    assert("fit check: horizontal overflow counts too", deckDom.slideOverflow(w), 180);
    // Sub-pixel rounding makes scrollHeight one more than clientHeight on a
    // slide that fits exactly; that is not an overflow worth a warning.
    var r = laidOut({ w: 1920, h: 1081 }); r.hidden = false;
    assert("fit check: one px of rounding is not overflow", deckDom.slideOverflow(r), 0);

    // Every slide is measured, not just the one on screen, and each keeps
    // the visibility it had.
    var a = laidOut(fits), b = laidOut(tall), c = laidOut(wide);
    a.hidden = false;
    var deck = stubDeck([a, b, c]);
    var over = deckDom.checkFit(deck);
    assertDeep("fit check: lists the overflowing slides by number", over, [2, 3]);
    assertDeep("fit check: visibility restored",
        [a.hidden, b.hidden, c.hidden], [false, true, true]);

    // The check only measures.  The viewer is not the deck's author, so
    // nothing on the slide may change (DR-16): fit_check.py reads the list.
    var flagged = queryAll(deck, ".fit-flag").length;
    assert("fit check: no slide gets a flag", flagged, 0);
    assertDeep("fit check: no slide gets a mark",
        [a.className, b.className, c.className],
        ["slide layout-default", "slide layout-default", "slide layout-default"]);

    // A slide that is not laid out measures 0x0, which would read as "fits".
    // It is left out of the list rather than counted either way.
    var gone = laidOut(tall, { w: 0, h: 0 });
    var deck2 = stubDeck([gone, laidOut(tall)]);
    assertDeep("fit check: an unmeasured slide is not listed",
        deckDom.checkFit(deck2), [2]);

    // A hidden deck (the standalone page's markdown view) measures 0x0 and
    // says nothing about its slides, so the answer is "unknown".
    deck.clientWidth = 0; deck.clientHeight = 0;
    assert("fit check: a hidden deck is not measured", deckDom.checkFit(deck), null);
})();

// 12. Only the measurement is exported.  The viewer-side flagging (DR-10)
//     is gone, so a page cannot bring it back by calling it (DR-16).
(function () {
    assert("fit check: no flagSlide", typeof deckDom.flagSlide, "undefined");
    assert("fit check: no watchFit", typeof deckDom.watchFit, "undefined");
})();

// 13. A slide with regions (DR-11, two-column) nests each row in the region
//     whose start/end slice holds it, in order.  The region names are
//     view_specs' own, so nothing here inspects the layout.  A slide with no
//     regions keeps its rows directly under the section, as before.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs({
        slides: [
            { index: 0, number: 1, layout: "two-column",
              rows: [row("<h2>T</h2>", 1, 1), row("<p>a</p>", 3, 3),
                     row("<p>b</p>", 7, 7), row("<p>c</p>", 9, 9)],
              regions: [{ name: "head", start: 0, end: 1 },
                        { name: "left", start: 1, end: 2 },
                        { name: "right", start: 2, end: 4 }] },
            { index: 1, number: 2, layout: "default",
              rows: [row("<p>d</p>", 11, 11)], regions: [] },
        ],
    }));
    var sections = deck.querySelectorAll("section.slide");
    var regions = sections[0].children.filter(function (c) {
        return c.tag === "div" && /\bslide-region\b/.test(c.className);
    });
    assertDeep("regions: one div per region, named by the spec",
        regions.map(function (r) { return r.className; }),
        ["slide-region region-head", "slide-region region-left",
         "slide-region region-right"]);
    assertDeep("regions: each holds its slice of the rows",
        regions.map(function (r) {
            return r.children.map(function (b) { return b.innerHTML; });
        }),
        [["<h2>T</h2>"], ["<p>a</p>"], ["<p>b</p>", "<p>c</p>"]]);
    assert("regions: blocks keep their anchor",
        regions[2] && regions[2].children[1].getAttribute("data-line-start"), "9");
    assert("regions: no block left directly on the slide",
        sections[0].children.filter(function (c) {
            return c.className === "slide-block";
        }).length, 0);
    assert("regions: a plain slide keeps its blocks on the section",
        sections[1].children[0].className, "slide-block");
})();

// 14. Specs from before DR-11 carry no `regions` key at all, and still build.
(function () {
    var deck = deckDom.buildDeck(fakeDocument, specs());
    assert("regions: absent key builds flat",
        deck.querySelectorAll("section.slide")[1].children[0].className, "slide-block");
})();

// 15. Slide chrome (DR-12): a top bar naming the deck and a footer line on
//     every slide, carrying the text view_specs settled.  textContent, never
//     innerHTML, because the text is the deck author's front matter.
(function () {
    var deck = deckDom.buildDeck(fakeDocument,
        specs({ chrome: { header: "<b>Deck</b>", footer: "ACME" } }));
    var headers = deck.querySelectorAll("div.slide-header");
    var footers = deck.querySelectorAll("div.slide-footer");
    assert("chrome: one top bar per slide", headers.length, 2);
    assert("chrome: one footer per slide", footers.length, 2);
    assert("chrome: top bar text is text", headers[1].textContent, "<b>Deck</b>");
    assert("chrome: top bar html untouched", headers[1].innerHTML, null);
    assert("chrome: footer text", footers[0].textContent, "ACME");
    assert("chrome: not a slide block",
        deck.querySelectorAll("div.slide-block").length, 3);
})();

// 16. An empty top bar is left out; the footer line stays, as the frame's
//     bottom edge.
(function () {
    var deck = deckDom.buildDeck(fakeDocument,
        specs({ chrome: { header: "", footer: "" } }));
    assert("chrome: empty header left out",
        deck.querySelectorAll("div.slide-header").length, 0);
    assert("chrome: footer line kept",
        deck.querySelectorAll("div.slide-footer").length, 2);
})();

// 17. Chrome off, or specs from before DR-12 with no key: no frame at all.
(function () {
    [specs({ chrome: null }), specs()].forEach(function (s, i) {
        var deck = deckDom.buildDeck(fakeDocument, s);
        assert("chrome off " + i + ": no top bar",
            deck.querySelectorAll("div.slide-header").length, 0);
        assert("chrome off " + i + ": no footer",
            deck.querySelectorAll("div.slide-footer").length, 0);
    });
})();

if (failures.length) {
    console.error("FAIL (" + failures.length + "):");
    failures.forEach(function (f) { console.error("  - " + f); });
    process.exit(1);
}
console.log("test_deck_dom.js: all pass");
