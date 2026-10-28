/* doc-review — selection-anchored comments (DR-7).
 *
 * A comment can carry a `quote`: the text the reviewer selected inside the
 * block before clicking it.  The quote is stored with the comment and
 * highlighted wherever the block is rendered.  A comment without one is on
 * the whole block, as every comment written before DR-7 is.
 *
 * The matching rules (quoteSegments, selectionQuote, quotesForBlock) are
 * DOM-free so test_quote_mark.js can `require()` the code the browser runs.
 * markQuotes is the thin DOM half; app.js consumes all of it as
 * `window.docReviewQuote`.
 */
(function (root) {
    "use strict";

    /* Mirrors MAX_QUOTE_CHARS in server.py.  A longer selection would be
     * refused with a 422 and cost the reviewer the comment they typed, so it
     * anchors to the block instead. */
    var MAX_QUOTE_CHARS = 2000;

    function escapeRegExp(s) {
        return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }

    /* Where `quote` first appears across a block's text nodes, as one
     * {node, start, end} slice per node it touches.  `texts` holds each text
     * node's data in document order.  Whitespace runs match each other,
     * because Selection.toString() and the DOM disagree on soft line breaks. */
    function quoteSegments(texts, quote) {
        var q = (quote || "").trim();
        if (!q) return [];
        var pattern = q.split(/\s+/).map(escapeRegExp).join("\\s+");
        var m = new RegExp(pattern).exec(texts.join(""));
        if (!m) return [];
        var from = m.index;
        var to = m.index + m[0].length;
        var segments = [];
        var offset = 0;
        for (var i = 0; i < texts.length; i++) {
            var len = texts[i].length;
            var start = Math.max(from, offset) - offset;
            var end = Math.min(to, offset + len) - offset;
            if (start < end) segments.push({ node: i, start: start, end: end });
            offset += len;
        }
        return segments;
    }

    /* The quote a click on `cell` should carry, or "" for the whole block.
     * Only a selection that starts and ends inside the clicked block counts;
     * one reaching into a neighbour is not about this block. */
    function selectionQuote(sel, cell) {
        if (!sel || sel.isCollapsed || !cell) return "";
        if (!cell.contains(sel.anchorNode) || !cell.contains(sel.focusNode)) {
            return "";
        }
        var text = String(sel).trim();
        if (text.length > MAX_QUOTE_CHARS) return "";
        return text;
    }

    /* Quotes a block highlights: open comments only.  A detached comment's
     * text is gone from this block, so highlighting a lookalike would claim
     * a passage it was never about. */
    function quotesForBlock(comments) {
        var out = [];
        for (var i = 0; i < (comments || []).length; i++) {
            var c = comments[i];
            if (c.quote && !c.resolved && !c.detached) out.push(c.quote);
        }
        return out;
    }

    /* Rendered diagrams are SVG, and mermaid reads its source from text that
     * must stay unwrapped; neither may gain a <mark>. */
    var SKIP = "svg, .mermaid, script, style, textarea";

    /* markdown-it leaves "\n" text nodes between block elements: list items,
     * table cells, the paragraphs of a quote or a loose item.  They stay in
     * the text a quote is matched against, so a selection across two items
     * still matches, but whitespace alone is never wrapped.  A <mark> between
     * two blocks is an anonymous box: a phantom blank line or table column.
     * The cost inside a sentence is an unhighlighted space between two marks. */
    var BLANK = /^\s*$/;

    function textNodes(cell, doc) {
        var nodes = [];
        var walker = doc.createTreeWalker(cell, 4 /* NodeFilter.SHOW_TEXT */);
        var n;
        while ((n = walker.nextNode())) {
            if (n.parentElement && n.parentElement.closest(SKIP)) continue;
            nodes.push(n);
        }
        return nodes;
    }

    /* Wrap each quote's first occurrence in `cell` in <mark class="comment-quote">.
     * Marks from an earlier pass are removed first, so calling this again
     * after a re-render never nests them. */
    function markQuotes(cell, quotes, doc) {
        var old = cell.querySelectorAll("mark.comment-quote");
        for (var i = 0; i < old.length; i++) {
            var mark = old[i];
            while (mark.firstChild) mark.parentNode.insertBefore(mark.firstChild, mark);
            mark.remove();
        }
        if (old.length) cell.normalize();

        for (var q = 0; q < quotes.length; q++) {
            var nodes = textNodes(cell, doc);
            var segs = quoteSegments(
                nodes.map(function (t) { return t.data; }), quotes[q]
            );
            for (var s = 0; s < segs.length; s++) {
                var t = nodes[segs[s].node];
                if (BLANK.test(t.data.slice(segs[s].start, segs[s].end))) continue;
                if (segs[s].end < t.data.length) t.splitText(segs[s].end);
                var mid = segs[s].start > 0 ? t.splitText(segs[s].start) : t;
                var wrap = doc.createElement("mark");
                wrap.className = "comment-quote";
                mid.parentNode.insertBefore(wrap, mid);
                wrap.appendChild(mid);
            }
        }
    }

    var api = {
        MAX_QUOTE_CHARS: MAX_QUOTE_CHARS,
        quoteSegments: quoteSegments,
        selectionQuote: selectionQuote,
        quotesForBlock: quotesForBlock,
        markQuotes: markQuotes,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewQuote = api;
    }
})(typeof window !== "undefined" ? window : this);
