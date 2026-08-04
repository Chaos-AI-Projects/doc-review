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

    /* The row/TOC/header render-spec builders that used to live here were
     * ported to Python in view_specs.py (#451): the server-side Jinja render
     * and the client soft swap now share one implementation, so they cannot
     * drift.  They are reached through the warm Pyodide runtime
     * (window.docReviewRenderer) — safe, because a soft swap only happens once
     * the renderer is warm.  The routing logic below deliberately stays in JS:
     * it decides what happens BEFORE the renderer is warm. */

    var api = {
        viewUrl: viewUrl,
        apiSourceUrl: apiSourceUrl,
        shouldIntercept: shouldIntercept,
        popstateAction: popstateAction,
        lineAnchorId: lineAnchorId,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewNavLogic = api;
    }
})(typeof window !== "undefined" ? window : this);
