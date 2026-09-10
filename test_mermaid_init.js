#!/usr/bin/env node
/**
 * Behavioral test for mermaid container rendering (MS-605).
 *
 * Loads the *real* module the browser runs (static/mermaid_init.js) rather
 * than a mirrored copy, following test_spa_nav.js.  The module takes the root
 * to scan and the render function to call, so both the review DOM and the
 * presentation deck can be exercised here without a browser.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

var path = require("path");
var mermaidInit = require(path.join(__dirname, "static", "mermaid_init.js"));

var failures = [];

function assert(label, actual, expected) {
    if (actual !== expected) {
        failures.push(label + ": expected " + expected + ", got " + actual);
    }
}

function assertDeep(label, actual, expected) {
    var a = JSON.stringify(actual);
    var e = JSON.stringify(expected);
    if (a !== e) failures.push(label + ":\n      expected " + e + "\n      got      " + a);
}

/* ── A DOM stub just deep enough to be wrong in the ways the real DOM was ──
 *
 * querySelectorAll walks descendants and matches "tag", ".class" or
 * "tag.class", so a selector scoped to the review table genuinely misses the
 * deck, exactly as it did in the browser.
 */

function node(tag, className, children) {
    return {
        tag: tag,
        className: className || "",
        children: children || [],
        textContent: "",
        style: {},
        innerHTML: null,
        querySelectorAll: function (sel) { return queryAll(this, sel); },
        querySelector: function (sel) { return queryAll(this, sel)[0] || null; },
    };
}

function text(tag, className, content) {
    var n = node(tag, className, []);
    n.textContent = content;
    return n;
}

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

/* A fake mermaid.render: succeeds, and records the source it was handed. */
function recordingRender(seen) {
    return function (id, src) {
        seen.push(src);
        return Promise.resolve({ svg: "<svg data-src=\"" + src + "\"></svg>" });
    };
}

/* ── Fixtures ── */

// Review mode: one block per table row, container inside td.line-content.
function reviewDom(sources) {
    var rows = sources.map(function (s) {
        return node("tr", "source-line", [
            node("td", "line-content", [text("div", "mermaid", s)]),
        ]);
    });
    return node("table", "", rows);
}

// Presentation mode: containers live in .slide-block divs inside section.slide.
function deckDom(sources) {
    var blocks = sources.map(function (s) {
        return node("div", "slide-block", [text("div", "mermaid", s)]);
    });
    return node("div", "presentation", [node("section", "slide", blocks)]);
}

/* ── Cases ── */

var pending = [];

// 1. The deck is the bug: a container inside a slide must be rendered.
(function () {
    var seen = [];
    var root = deckDom(["graph TD\n    A-->B"]);
    pending.push(
        mermaidInit.renderMermaid(root, recordingRender(seen)).then(function (count) {
            assert("deck: one container rendered", count, 1);
            assertDeep("deck: rendered from the slide's own source",
                seen, ["graph TD\n    A-->B"]);
            var container = root.querySelectorAll(".mermaid")[0];
            assert("deck: container holds svg, not raw source",
                container.innerHTML, "<svg data-src=\"graph TD\n    A-->B\"></svg>");
        })
    );
})();

// 2. Review mode keeps working.
(function () {
    var seen = [];
    var root = reviewDom(["graph LR\n    X-->Y"]);
    pending.push(
        mermaidInit.renderMermaid(root, recordingRender(seen)).then(function (count) {
            assert("review: one container rendered", count, 1);
            assertDeep("review: rendered from its own source",
                seen, ["graph LR\n    X-->Y"]);
        })
    );
})();

// 3. Adjacent fences are two diagrams, not one.  The old grouping joined
//    consecutive cells into a single source and hid all but the first, which
//    predates one-block-per-row and silently ate the second diagram.
(function () {
    var seen = [];
    var root = reviewDom(["graph TD\n    A-->B", "graph TD\n    C-->D"]);
    pending.push(
        mermaidInit.renderMermaid(root, recordingRender(seen)).then(function (count) {
            assert("adjacent: both containers rendered", count, 2);
            assertDeep("adjacent: each fence keeps its own source",
                seen, ["graph TD\n    A-->B", "graph TD\n    C-->D"]);
            var containers = root.querySelectorAll(".mermaid");
            assert("adjacent: second container is not hidden",
                containers[1].style.display, undefined);
            assert("adjacent: second container holds its own svg",
                containers[1].innerHTML, "<svg data-src=\"graph TD\n    C-->D\"></svg>");
        })
    );
})();

// 4. A parse error leaves the raw source visible rather than blanking it, and
//    does not stop the diagrams after it.
(function () {
    var seen = [];
    var root = reviewDom(["!!! not a diagram", "graph TD\n    A-->B"]);
    var render = function (id, src) {
        if (src.indexOf("!!!") === 0) return Promise.reject(new Error("parse error"));
        seen.push(src);
        return Promise.resolve({ svg: "<svg></svg>" });
    };
    pending.push(
        mermaidInit.renderMermaid(root, render).then(function (count) {
            assert("error: only the good diagram counts", count, 1);
            var containers = root.querySelectorAll(".mermaid");
            assert("error: bad container untouched, raw text still shown",
                containers[0].innerHTML, null);
            assertDeep("error: the later diagram still renders", seen,
                ["graph TD\n    A-->B"]);
        })
    );
})();

// 5. No containers is a no-op, and must not call the renderer at all — the
//    caller uses that to avoid importing mermaid from the CDN.
(function () {
    var called = 0;
    var root = node("div", "", []);
    pending.push(
        mermaidInit.renderMermaid(root, function () {
            called++;
            return Promise.resolve({ svg: "" });
        }).then(function (count) {
            assert("empty: nothing rendered", count, 0);
            assert("empty: renderer never called", called, 0);
        })
    );
})();

// 6. Scoping: rendering the deck must not reach into the review DOM, whose
//    containers already hold SVG whose textContent is not diagram source.
(function () {
    var seen = [];
    var review = reviewDom(["graph TD\n    REVIEW-->ONLY"]);
    var deck = deckDom(["graph TD\n    DECK-->ONLY"]);
    var body = node("body", "", [review, deck]);
    pending.push(
        mermaidInit.renderMermaid(deck, recordingRender(seen)).then(function () {
            assertDeep("scoped: only the deck's source was rendered",
                seen, ["graph TD\n    DECK-->ONLY"]);
            assert("scoped: the review container was left alone",
                body.querySelectorAll(".mermaid")[0].innerHTML, null);
        })
    );
})();

Promise.all(pending).then(function () {
    if (failures.length) {
        console.error("FAILED (" + failures.length + "):");
        failures.forEach(function (f) { console.error("  - " + f); });
        process.exit(1);
    }
    console.log("All mermaid init tests passed.");
}).catch(function (err) {
    console.error("Test harness error: " + (err && err.stack || err));
    process.exit(1);
});
