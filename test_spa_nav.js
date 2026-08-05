#!/usr/bin/env node
/**
 * Behavioral test for the SPA file-switching logic (#447).
 *
 * Loads the *real* module the browser runs (static/nav_logic.js) rather than a
 * mirrored copy, so these assertions cannot drift from shipped behaviour.
 * app.js wires this logic into the DOM; that thin plumbing is covered by the
 * route-level assertions in test_server.py.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

var path = require("path");
var nav = require(path.join(__dirname, "static", "nav_logic.js"));

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

function evt(over) {
    var base = {
        defaultPrevented: false, button: 0, metaKey: false, ctrlKey: false,
        shiftKey: false, altKey: false,
    };
    for (var k in over) if (over.hasOwnProperty(k)) base[k] = over[k];
    return base;
}

// ── URLs: the scheme stays ?path=, never a fragment (#447 non-goal) ──

assert("viewUrl keeps the ?path= scheme",
    nav.viewUrl("kb/wiki/log.md"), "/view?path=kb%2Fwiki%2Flog.md");
assert("apiSourceUrl targets /api/source",
    nav.apiSourceUrl("kb/wiki/log.md"), "/api/source?path=kb%2Fwiki%2Flog.md");
assert("viewUrl encodes spaces", nav.viewUrl("a b.md"), "/view?path=a%20b.md");
assert("viewUrl encodes query-ish characters",
    nav.viewUrl("a&b?c.md"), "/view?path=a%26b%3Fc.md");
assert("viewUrl contains no fragment",
    nav.viewUrl("kb/wiki/log.md").indexOf("#"), -1);
assert("apiSourceUrl contains no fragment",
    nav.apiSourceUrl("kb/wiki/log.md").indexOf("#"), -1);

// ── Click interception ──

assert("plain click on another file is intercepted",
    nav.shouldIntercept(evt({}), "kb/wiki/log.md", "README.md", true), true);
assert("cold renderer falls through to a full page load",
    nav.shouldIntercept(evt({}), "kb/wiki/log.md", "README.md", false), false);
assert("ctrl+click falls through",
    nav.shouldIntercept(evt({ ctrlKey: true }), "a.md", "README.md", true), false);
assert("meta+click falls through",
    nav.shouldIntercept(evt({ metaKey: true }), "a.md", "README.md", true), false);
assert("shift+click falls through",
    nav.shouldIntercept(evt({ shiftKey: true }), "a.md", "README.md", true), false);
assert("alt+click falls through",
    nav.shouldIntercept(evt({ altKey: true }), "a.md", "README.md", true), false);
assert("middle click falls through",
    nav.shouldIntercept(evt({ button: 1 }), "a.md", "README.md", true), false);
assert("already-handled click falls through",
    nav.shouldIntercept(evt({ defaultPrevented: true }), "a.md", "README.md", true),
    false);
assert("click outside a nav link is ignored",
    nav.shouldIntercept(evt({}), null, "README.md", true), false);
assert("click on the current file is ignored",
    nav.shouldIntercept(evt({}), "a.md", "a.md", true), false);

// ── Back / Forward ──

assert("popstate to another file soft-navigates",
    nav.popstateAction("kb/wiki/log.md", "README.md", true), "soft");
assert("popstate without a warm renderer reloads",
    nav.popstateAction("kb/wiki/log.md", "README.md", false), "reload");
assert("popstate to the same path (hash-only change) is ignored",
    nav.popstateAction("a.md", "a.md", true), "ignore");
assert("popstate to a pathless URL reloads",
    nav.popstateAction("", "a.md", true), "reload");

// ── #L line anchors survive a swap ──

assert("line anchor is extracted from #L42", nav.lineAnchorId("#L42"), "L42");
assert("line anchor is extracted from #L1", nav.lineAnchorId("#L1"), "L1");
assert("empty hash has no anchor", nav.lineAnchorId(""), null);
assert("undefined hash has no anchor", nav.lineAnchorId(undefined), null);
assert("non-line fragment is not treated as an anchor",
    nav.lineAnchorId("#kb/wiki/log.md"), null);
assert("bare #L is not an anchor", nav.lineAnchorId("#L"), null);
assert("#Lfoo is not an anchor", nav.lineAnchorId("#Lfoo"), null);

// ── Presentation mode key handling (#452) ──
//
// The slide *grouping* is Python (view_specs.slide_specs, covered by
// test_presentation.py); only the keyboard decision stays in JS, because it
// must respond instantly and never depends on the renderer being warm.

assert("right arrow advances", nav.presentationAction("ArrowRight"), "next");
assert("down arrow advances", nav.presentationAction("ArrowDown"), "next");
assert("page down advances", nav.presentationAction("PageDown"), "next");
assert("space advances", nav.presentationAction(" "), "next");
assert("legacy Spacebar advances", nav.presentationAction("Spacebar"), "next");
assert("left arrow goes back", nav.presentationAction("ArrowLeft"), "prev");
assert("up arrow goes back", nav.presentationAction("ArrowUp"), "prev");
assert("page up goes back", nav.presentationAction("PageUp"), "prev");
assert("Escape leaves presentation mode", nav.presentationAction("Escape"), "exit");
assert("legacy Esc leaves presentation mode", nav.presentationAction("Esc"), "exit");
assert("an ordinary key is left alone", nav.presentationAction("a"), null);
assert("Tab is left alone", nav.presentationAction("Tab"), null);
assert("an undefined key is left alone", nav.presentationAction(undefined), null);

assert("slide index advances", nav.clampSlide(1, 3), 1);
assert("advancing past the last slide stays put",
    nav.clampSlide(3, 3), 2);
assert("going back past the first slide stays put",
    nav.clampSlide(-1, 3), 0);
assert("an empty deck clamps to 0", nav.clampSlide(2, 0), 0);

// ── On-screen controls (#455) ──
//
// A phone has no arrow keys and no Esc, so the deck carries prev/next/exit
// buttons.  They resolve to the SAME action vocabulary presentationAction()
// produces for keys, and app.js funnels both through one dispatcher — a second
// navigation path is exactly what would let pointer and keyboard drift apart.

assert("the prev control goes back",
    nav.presentationControlAction("prev"), "prev");
assert("the next control advances",
    nav.presentationControlAction("next"), "next");
assert("the exit control leaves presentation mode",
    nav.presentationControlAction("exit"), "exit");
assert("an unknown control name is inert",
    nav.presentationControlAction("advance"), null);
assert("a missing data-action is inert",
    nav.presentationControlAction(null), null);
assert("an empty data-action is inert",
    nav.presentationControlAction(""), null);

// Every keyboard action must have a pointer equivalent, or a phone user can
// reach a state they cannot get out of.  The key list is read out of the
// module source rather than hardcoded here: a key added to presentationAction()
// with no pointer twin has to fail this, and a hardcoded list would not notice.
var navSource = require("fs").readFileSync(
    path.join(__dirname, "static", "nav_logic.js"), "utf8");
var keyboardSwitch = navSource.slice(
    navSource.indexOf("function presentationAction("),
    navSource.indexOf("function presentationControlAction(")
);
var handledKeys = (keyboardSwitch.match(/case "[^"]*":/g) || [])
    .map(function (c) { return c.slice(6, -2); });

assert("the keyboard switch was actually located",
    handledKeys.length >= 10, true);
for (var k = 0; k < handledKeys.length; k++) {
    var keyAction = nav.presentationAction(handledKeys[k]);
    assert("key " + JSON.stringify(handledKeys[k]) + " -> " + keyAction +
        " has a pointer equivalent",
        nav.presentationControlAction(keyAction), keyAction);
}

// ── Fullscreen (#455) ──
//
// requestFullscreen() may be denied, unsupported (iOS Safari has no element
// fullscreen) or rejected outside a user gesture, in which case the deck stays
// a fixed overlay.  When it DID take and the browser then drops us out by its
// own gesture, the deck must not be left half-presented.

assert("leaving fullscreen while presenting exits the deck",
    nav.fullscreenChangeAction(true, true, null), "exit");
assert("entering fullscreen is not an exit",
    nav.fullscreenChangeAction(true, true, {}), null);
assert("a fullscreen change while not presenting is ignored",
    nav.fullscreenChangeAction(false, true, null), null);
assert("the overlay fallback is not dragged out by a foreign fullscreen change",
    nav.fullscreenChangeAction(true, false, null), null);
assert("another element going fullscreen does not exit the deck",
    nav.fullscreenChangeAction(true, false, {}), null);

// ── Render-spec builders moved to Python (#451) ──
//
// sourceRowSpecs / tocItemSpecs / headerFields / rowClass / lineLabel now live
// in view_specs.py so the Jinja render and the soft swap share one source of
// truth; their cases were ported to test_view_specs.py.  Assert the JS copies
// are really gone — two implementations is exactly the drift the port removes.

var ported = [
    "sourceRowSpecs", "tocItemSpecs", "headerFields", "rowClass", "lineLabel",
    // Slide grouping is Python too (#452) — grouping the same row specs is what
    // makes a comment resolve to the same block in both modes.
    "slideSpecs", "presentationSpecs", "frontMatterDirectives",
];
for (var p = 0; p < ported.length; p++) {
    assert("ported builder " + ported[p] + " is no longer exported by JS",
        typeof nav[ported[p]], "undefined");
}

// ── Report ──

if (failures.length > 0) {
    process.stderr.write("FAIL: " + failures.length + " assertion(s) failed:\n");
    for (var i = 0; i < failures.length; i++) {
        process.stderr.write("  - " + failures[i] + "\n");
    }
    process.exit(1);
} else {
    process.stdout.write("OK: all SPA navigation assertions passed\n");
    process.exit(0);
}
