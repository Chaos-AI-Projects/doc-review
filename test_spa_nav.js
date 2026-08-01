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

// ── Rebuilt source rows ──

var blocks = [
    { start_line: 1, end_line: 1, html: "<h1>T</h1>" },
    { start_line: 3, end_line: 5, html: "<p>body</p>" },
];
var commentsByBlock = { "3": [{ id: 1 }, { id: 2 }] };

assertDeep("row specs carry ids, classes, labels, html and comment counts",
    nav.sourceRowSpecs(blocks, commentsByBlock),
    [
        {
            id: "L1", rowClass: "source-line", startLine: 1, endLine: 1,
            label: "1", html: "<h1>T</h1>", commentCount: 0,
        },
        {
            id: "L3", rowClass: "source-line has-comments", startLine: 3,
            endLine: 5, label: "3-5", html: "<p>body</p>", commentCount: 2,
        },
    ]);
assertDeep("no comment data → no rows marked",
    nav.sourceRowSpecs(blocks, null).map(function (s) { return s.rowClass; }),
    ["source-line", "source-line"]);
assertDeep("no blocks → no rows", nav.sourceRowSpecs(null, {}), []);
assert("single-line label", nav.lineLabel({ start_line: 4, end_line: 4 }), "4");
assert("multi-line label", nav.lineLabel({ start_line: 4, end_line: 7 }), "4-7");
assert("plain row class", nav.rowClass(0), "source-line");
assert("commented row class", nav.rowClass(2), "source-line has-comments");

// ── Rebuilt TOC ──

assertDeep("toc items keep level classes and #L links",
    nav.tocItemSpecs([
        { level: 1, text: "Title", start_line: 1 },
        { level: 2, text: "Sub", start_line: 9 },
    ]),
    [
        { className: "toc-item toc-level-1", href: "#L1", text: "Title" },
        { className: "toc-item toc-level-2", href: "#L9", text: "Sub" },
    ]);
assertDeep("empty toc → no items", nav.tocItemSpecs([]), []);
assertDeep("missing toc → no items", nav.tocItemSpecs(undefined), []);

// ── Header + comment-form fields follow the swapped-in file ──

var fields = nav.headerFields({
    path: "kb/wiki/log.md",
    file_id: "0123456789abcdef0123456789abcdef",
});
assert("header title is the new path", fields.title, "kb/wiki/log.md");
assert("file id is truncated to 12 chars + ellipsis",
    fields.fileIdLabel, "0123456789ab\u2026");
assert("document title follows the new path",
    fields.documentTitle, "doc-review \u2014 kb/wiki/log.md");
assert("comment form posts the NEW file_id",
    fields.formFileId, "0123456789abcdef0123456789abcdef");
assert("comment form posts the NEW path", fields.formPath, "kb/wiki/log.md");

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
