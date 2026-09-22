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

if (failures.length) {
    console.error("FAIL (" + failures.length + "):");
    failures.forEach(function (f) { console.error("  - " + f); });
    process.exit(1);
}
console.log("test_deck_dom.js: all pass");
