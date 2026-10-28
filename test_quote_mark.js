#!/usr/bin/env node
/**
 * Behavioral test for selection-anchored comments (DR-7).
 *
 * Loads the real module the browser runs (static/quote_mark.js), following
 * test_spa_nav.js.  The matching rules are tested as plain data, and
 * markQuotes against a small DOM stub, since jsdom is not a dependency.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

var path = require("path");
var qm = require(path.join(__dirname, "static", "quote_mark.js"));

var failures = [];

function assertDeep(label, actual, expected) {
    var a = JSON.stringify(actual);
    var e = JSON.stringify(expected);
    if (a !== e) failures.push(label + ":\n      expected " + e + "\n      got      " + a);
}

/* ── quoteSegments: where a quote lands across a block's text nodes ── */

assertDeep("quote inside one text node",
    qm.quoteSegments(["The quick brown fox"], "quick brown"),
    [{ node: 0, start: 4, end: 15 }]);

// "a *b* c" renders as three text nodes; a selection across the <em> is one
// quote that must be wrapped piecewise, never by moving the <em>.
assertDeep("quote spanning an inline element",
    qm.quoteSegments(["one ", "two", " three"], "ne two th"),
    [
        { node: 0, start: 1, end: 4 },
        { node: 1, start: 0, end: 3 },
        { node: 2, start: 0, end: 3 },
    ]);

assertDeep("first occurrence only",
    qm.quoteSegments(["ab ab"], "ab"),
    [{ node: 0, start: 0, end: 2 }]);

assertDeep("text that changed since the comment: no mark",
    qm.quoteSegments(["The slow fox"], "quick"),
    []);

assertDeep("empty quote marks nothing",
    qm.quoteSegments(["anything"], ""),
    []);

assertDeep("null quote marks nothing",
    qm.quoteSegments(["anything"], null),
    []);

// The browser's Selection.toString() and the DOM's text disagree on runs of
// whitespace (a soft line break in a paragraph is "\n" in one, " " in the
// other), so whitespace runs match each other.
assertDeep("whitespace runs match across a line break",
    qm.quoteSegments(["first line\nsecond line"], "line second"),
    [{ node: 0, start: 6, end: 17 }]);

assertDeep("whitespace run spanning nodes",
    qm.quoteSegments(["end of one ", " start"], "one start"),
    [
        { node: 0, start: 7, end: 11 },
        { node: 1, start: 0, end: 6 },
    ]);

assertDeep("regex metacharacters are literal",
    qm.quoteSegments(["cost is $5 (approx.)"], "$5 (approx.)"),
    [{ node: 0, start: 8, end: 20 }]);

/* ── selectionQuote: what a click turns into ── */

function cell(owned) {
    return { contains: function (n) { return owned.indexOf(n) !== -1; } };
}

function selection(anchor, focus, text) {
    return {
        isCollapsed: anchor === focus && text === "",
        anchorNode: anchor,
        focusNode: focus,
        toString: function () { return text; },
    };
}

var a = {}, b = {}, outside = {};

assertDeep("selection inside the clicked block",
    qm.selectionQuote(selection(a, b, "  brown fox "), cell([a, b])),
    "brown fox");

assertDeep("no selection falls back to the block",
    qm.selectionQuote(selection(a, a, ""), cell([a])),
    "");

assertDeep("selection reaching into another block falls back",
    qm.selectionQuote(selection(a, outside, "fox and more"), cell([a])),
    "");

assertDeep("whitespace-only selection falls back",
    qm.selectionQuote(selection(a, b, " \n "), cell([a, b])),
    "");

assertDeep("missing selection API falls back",
    qm.selectionQuote(null, cell([a])),
    "");

// Over the server's MAX_QUOTE_CHARS the post would be refused with a 422,
// losing the comment body the reviewer typed.  Anchor to the block instead.
var long = new Array(2002).join("x");
assertDeep("selection over the server cap falls back",
    qm.selectionQuote(selection(a, b, long), cell([a, b])),
    "");

/* ── quotesByBlock: which quotes a row highlights ── */

assertDeep("unresolved quoted comments only",
    qm.quotesForBlock([
        { quote: "alpha", resolved: 0 },
        { quote: null, resolved: 0 },
        { quote: "beta", resolved: 1 },
        { quote: "gamma", resolved: 0, detached: true },
        { quote: "delta" },
    ]),
    ["alpha", "delta"]);

assertDeep("no comments",
    qm.quotesForBlock(undefined),
    []);

/* ── markQuotes: the DOM half, on a stub just deep enough to split text ──
 *
 * Elements and text nodes with the handful of APIs markQuotes calls.  The
 * markup mirrors what renderer.py emits, including the "\n" text nodes
 * markdown-it leaves between <li>, <tr> and <td> elements.
 */

function T(data) {
    return {
        nodeType: 3, data: data, parentNode: null,
        get parentElement() { return this.parentNode; },
        get length() { return this.data.length; },
        splitText: function (at) {
            var rest = T(this.data.slice(at));
            this.data = this.data.slice(0, at);
            var p = this.parentNode, i = p.childNodes.indexOf(this);
            p.childNodes.splice(i + 1, 0, rest);
            rest.parentNode = p;
            return rest;
        },
    };
}

function E(tag, className, kids) {
    var el = {
        nodeType: 1, nodeName: tag.toUpperCase(), className: className || "",
        childNodes: [], parentNode: null,
        get firstChild() { return this.childNodes[0] || null; },
        appendChild: function (n) { return this.insertBefore(n, null); },
        insertBefore: function (n, ref) {
            if (n.parentNode) n.parentNode.childNodes.splice(n.parentNode.childNodes.indexOf(n), 1);
            var i = ref ? this.childNodes.indexOf(ref) : this.childNodes.length;
            this.childNodes.splice(i, 0, n);
            n.parentNode = this;
            return n;
        },
        remove: function () {
            this.parentNode.childNodes.splice(this.parentNode.childNodes.indexOf(this), 1);
            this.parentNode = null;
        },
        closest: function (sel) {
            var tags = sel.split(/\s*,\s*/);
            for (var n = this; n; n = n.parentNode) {
                for (var i = 0; i < tags.length; i++) {
                    var t = tags[i];
                    if (t[0] === "." ? (" " + n.className + " ").indexOf(" " + t.slice(1) + " ") !== -1
                                     : n.nodeName === t.toUpperCase()) return n;
                }
            }
            return null;
        },
        querySelectorAll: function () {  // only ever "mark.comment-quote"
            return walk(this).filter(function (n) {
                return n.nodeName === "MARK" && n.className === "comment-quote";
            });
        },
        normalize: function () {
            for (var i = this.childNodes.length - 1; i > 0; i--) {
                var a = this.childNodes[i - 1], b = this.childNodes[i];
                if (a.nodeType === 3 && b.nodeType === 3) { a.data += b.data; this.childNodes.splice(i, 1); }
            }
            this.childNodes.forEach(function (c) { if (c.nodeType === 1) c.normalize(); });
        },
    };
    (kids || []).forEach(function (k) { el.appendChild(typeof k === "string" ? T(k) : k); });
    return el;
}

function walk(root) {
    var out = [];
    root.childNodes.forEach(function (c) { out.push(c); if (c.nodeType === 1) out = out.concat(walk(c)); });
    return out;
}

var doc = {
    createElement: function (tag) { return E(tag); },
    createTreeWalker: function (root) {
        var texts = walk(root).filter(function (n) { return n.nodeType === 3; }), i = 0;
        return { nextNode: function () { return texts[i++] || null; } };
    },
};

function html(n) {
    if (n.nodeType === 3) return n.data.replace(/\n/g, "\\n");
    var tag = n.nodeName.toLowerCase();
    var cls = n.className ? ' class="' + n.className + '"' : "";
    return "<" + tag + cls + ">" + n.childNodes.map(html).join("") + "</" + tag + ">";
}

var cellP = E("td", "line-content", [E("p", "", ["one ", E("em", "", ["two"]), " three"])]);
qm.markQuotes(cellP, ["ne two th"], doc);
assertDeep("mark spans an inline element piecewise", html(cellP),
    '<td class="line-content"><p>o<mark class="comment-quote">ne </mark>' +
    '<em><mark class="comment-quote">two</mark></em>' +
    '<mark class="comment-quote"> th</mark>ree</p></td>');

qm.markQuotes(cellP, ["ne two th"], doc);
qm.markQuotes(cellP, ["ne two th"], doc);
assertDeep("re-marking never nests", html(cellP),
    '<td class="line-content"><p>o<mark class="comment-quote">ne </mark>' +
    '<em><mark class="comment-quote">two</mark></em>' +
    '<mark class="comment-quote"> th</mark>ree</p></td>');

qm.markQuotes(cellP, [], doc);
assertDeep("no quotes restores the original text", html(cellP),
    '<td class="line-content"><p>one <em>two</em> three</p></td>');

// A <mark> directly inside <ul> or <tr> is an anonymous box: a phantom
// blank line in a list, a phantom column or row in a table.
var cellList = E("td", "line-content", [E("ul", "", ["\n", E("li", "", ["item one"]), "\n", E("li", "", ["item two"]), "\n"])]);
qm.markQuotes(cellList, ["item one item two"], doc);
assertDeep("list: no mark between items", html(cellList),
    '<td class="line-content"><ul>\\n<li><mark class="comment-quote">item one</mark></li>' +
    '\\n<li><mark class="comment-quote">item two</mark></li>\\n</ul></td>');

var cellTable = E("td", "line-content", [E("table", "", ["\n", E("tbody", "", ["\n",
    E("tr", "", ["\n", E("td", "", ["c"]), "\n", E("td", "", ["d"]), "\n"]), "\n"])])]);
qm.markQuotes(cellTable, ["c d"], doc);
assertDeep("table: no mark between cells", html(cellTable),
    '<td class="line-content"><table>\\n<tbody>\\n<tr>\\n<td><mark class="comment-quote">c</mark></td>' +
    '\\n<td><mark class="comment-quote">d</mark></td>\\n</tr>\\n</tbody></table></td>');

// The same "\n" sits between block children of a blockquote or a loose
// list item, so no parent list is complete: whitespace alone is never marked.
var cellQuote = E("td", "line-content", [E("blockquote", "", ["\n", E("p", "", ["first para"]), "\n", E("p", "", ["second para"]), "\n"])]);
qm.markQuotes(cellQuote, ["first para second para"], doc);
assertDeep("blockquote: no mark between paragraphs", html(cellQuote),
    '<td class="line-content"><blockquote>\\n<p><mark class="comment-quote">first para</mark></p>' +
    '\\n<p><mark class="comment-quote">second para</mark></p>\\n</blockquote></td>');

var cellLoose = E("td", "line-content", [E("ul", "", ["\n",
    E("li", "", ["\n", E("p", "", ["item a"]), "\n"]), "\n",
    E("li", "", ["\n", E("p", "", ["item b"]), "\n"]), "\n"])]);
qm.markQuotes(cellLoose, ["item a item b"], doc);
assertDeep("loose list: no mark beside a paragraph in an item", html(cellLoose),
    '<td class="line-content"><ul>\\n<li>\\n<p><mark class="comment-quote">item a</mark></p>\\n</li>' +
    '\\n<li>\\n<p><mark class="comment-quote">item b</mark></p>\\n</li>\\n</ul></td>');

var cellMermaid = E("td", "line-content", [E("div", "mermaid", ["graph TD; A-->B"])]);
qm.markQuotes(cellMermaid, ["A-->B"], doc);
assertDeep("diagram source stays unwrapped", html(cellMermaid),
    '<td class="line-content"><div class="mermaid">graph TD; A-->B</div></td>');

if (failures.length) {
    process.stderr.write("FAIL test_quote_mark.js\n  " + failures.join("\n  ") + "\n");
    process.exit(1);
}
process.stdout.write("ok test_quote_mark.js\n");
