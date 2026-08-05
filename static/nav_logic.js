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

    /* Presentation mode (#452): what a keypress means while presenting.
     * "next" | "prev" | "exit" | null (null = leave the key alone). */
    function presentationAction(key) {
        switch (key) {
        case "ArrowRight":
        case "ArrowDown":
        case "PageDown":
        case " ":
        case "Spacebar":  /* legacy key name */
            return "next";
        case "ArrowLeft":
        case "ArrowUp":
        case "PageUp":
            return "prev";
        case "Escape":
        case "Esc":       /* legacy key name */
            return "exit";
        default:
            return null;
        }
    }

    /* Presentation mode (#455): what a press on an on-screen control means.
     * A phone has no arrow keys and no Esc, so the deck also carries prev /
     * next / exit buttons.  They resolve into the SAME vocabulary
     * presentationAction() produces for keys — app.js then funnels both
     * through one dispatcher, so a tap and a keypress cannot drift into
     * disagreeing about what "next" does.  An unrecognised data-action is
     * inert rather than a guess. */
    function presentationControlAction(name) {
        switch (name) {
        case "next":
        case "prev":
        case "exit":
            return name;
        default:
            return null;
        }
    }

    /* Presentation mode (#455): what a `fullscreenchange` means.
     * "exit" | null (null = leave the deck as it is).
     *
     * Entering fullscreen is an enhancement that can be denied, unsupported
     * (iOS Safari has no element fullscreen) or rejected outside a user
     * gesture, in which case the deck presents as a fixed overlay and no
     * fullscreen state is ours to lose — hence `usedFullscreen`.  When it DID
     * take and the browser then drops us out by its own gesture (Esc, the
     * system chrome), the deck must leave presentation mode rather than sit in
     * a half-state under the site header. */
    function fullscreenChangeAction(presenting, usedFullscreen, fullscreenElement) {
        if (!presenting || !usedFullscreen) return null;
        return fullscreenElement ? null : "exit";
    }

    /* Keep a slide index inside the deck; advancing off either end stays put
     * rather than wrapping (a wrap mid-talk looks like a lost slide). */
    function clampSlide(index, count) {
        if (!count || count < 1) return 0;
        if (index < 0) return 0;
        return index > count - 1 ? count - 1 : index;
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
        presentationAction: presentationAction,
        presentationControlAction: presentationControlAction,
        fullscreenChangeAction: fullscreenChangeAction,
        clampSlide: clampSlide,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewNavLogic = api;
    }
})(typeof window !== "undefined" ? window : this);
