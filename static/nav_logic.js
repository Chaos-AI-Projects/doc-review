/* doc-review — pure navigation/render logic for SPA file switching (#447).
 *
 * Deliberately DOM-free: every function here is a plain data transform, so
 * test_spa_nav.js can `require()` and exercise the *same* code the browser
 * runs (app.js consumes this as `window.docReviewNavLogic`) instead of
 * testing a mirrored copy.
 */
(function (root) {
    "use strict";

    function viewUrl(path) {
        return "/view?path=" + encodeURIComponent(path);
    }

    function apiSourceUrl(path) {
        return "/api/source?path=" + encodeURIComponent(path);
    }

    /* Should a nav-link click be turned into a soft swap?  Modified clicks and
     * non-primary buttons keep their native behaviour, and without a warm
     * renderer we let the browser do the full-page /view navigation. */
    function shouldIntercept(evt, path, currentPath, rendererReady) {
        if (evt.defaultPrevented || evt.button !== 0) return false;
        if (evt.metaKey || evt.ctrlKey || evt.shiftKey || evt.altKey) return false;
        if (!path || path === currentPath) return false;
        return !!rendererReady;
    }

    /* "soft" | "ignore" | "reload" for a popstate event. */
    function popstateAction(urlPath, currentPath, rendererReady) {
        if (!urlPath) return "reload";
        if (urlPath === currentPath) return "ignore";
        return rendererReady ? "soft" : "reload";
    }

    /* Row id to scroll to after a swap: the "#L42" line anchor when the URL
     * carries one, else null (meaning: go to the top of the document). */
    function lineAnchorId(hash) {
        return /^#L\d+$/.test(hash || "") ? String(hash).slice(1) : null;
    }

    function rowClass(commentCount) {
        return "source-line" + (commentCount ? " has-comments" : "");
    }

    function lineLabel(block) {
        return block.end_line !== block.start_line
            ? block.start_line + "-" + block.end_line
            : String(block.start_line);
    }

    /* Describe the source table rows for a rendered document. */
    function sourceRowSpecs(blocks, commentsByBlock) {
        var byBlock = commentsByBlock || {};
        var specs = [];
        for (var i = 0; i < (blocks || []).length; i++) {
            var b = blocks[i];
            var count = (byBlock[String(b.start_line)] || []).length;
            specs.push({
                id: "L" + b.start_line,
                rowClass: rowClass(count),
                startLine: b.start_line,
                endLine: b.end_line,
                label: lineLabel(b),
                html: b.html,
                commentCount: count,
            });
        }
        return specs;
    }

    function tocItemSpecs(toc) {
        var specs = [];
        for (var i = 0; i < (toc || []).length; i++) {
            var entry = toc[i];
            specs.push({
                className: "toc-item toc-level-" + entry.level,
                href: "#L" + entry.start_line,
                text: entry.text,
            });
        }
        return specs;
    }

    /* Header + comment-form fields for a freshly loaded document.  The form
     * fields must follow the new file or comments would be posted against the
     * previously viewed one. */
    function headerFields(data) {
        return {
            title: data.path,
            fileIdLabel: String(data.file_id).slice(0, 12) + "\u2026",
            documentTitle: "doc-review \u2014 " + data.path,
            formFileId: data.file_id,
            formPath: data.path,
        };
    }

    var api = {
        viewUrl: viewUrl,
        apiSourceUrl: apiSourceUrl,
        shouldIntercept: shouldIntercept,
        popstateAction: popstateAction,
        lineAnchorId: lineAnchorId,
        rowClass: rowClass,
        lineLabel: lineLabel,
        sourceRowSpecs: sourceRowSpecs,
        tocItemSpecs: tocItemSpecs,
        headerFields: headerFields,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewNavLogic = api;
    }
})(typeof window !== "undefined" ? window : this);
