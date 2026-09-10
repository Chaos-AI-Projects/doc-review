/* doc-review — mermaid container rendering, shared by review and presentation
 * mode (MS-605).
 *
 * The CDN import and mermaid's own configuration stay at the call site in
 * view.html; this module only walks a root for `.mermaid` containers and swaps
 * each one's escaped source for the SVG a supplied render function returns.
 * That keeps it dependency-free, so test_mermaid_init.js can `require()` and
 * exercise the *same* code the browser runs (view.html consumes it as
 * `window.docReviewMermaid`) instead of testing a mirrored copy.
 *
 * The root argument is what makes the deck work.  The review DOM is hidden
 * rather than destroyed when presenting, and its containers already hold SVG
 * whose textContent is not diagram source, so the deck must be scanned on its
 * own.
 */
(function (root) {
    "use strict";

    /* Render every `.mermaid` container under `rootEl`, resolving to the
     * number rendered.
     *
     * Each container is one fenced block: renderer.py emits exactly one
     * `<div class="mermaid">` per ```mermaid fence, holding that whole fence.
     * So containers are rendered independently.  An earlier version joined
     * *consecutive* containers into a single diagram, which predates
     * one-block-per-row and merged two adjacent fences into one, hiding the
     * second.
     */
    function renderMermaid(rootEl, render) {
        var containers = rootEl.querySelectorAll(".mermaid");
        var rendered = 0;

        var step = function (i) {
            if (i >= containers.length) return Promise.resolve(rendered);
            var container = containers[i];
            var src = container.textContent;
            var id = "mermaid-" + Math.random().toString(36).slice(2, 8);
            return Promise.resolve()
                .then(function () { return render(id, src); })
                .then(function (result) {
                    container.innerHTML = result.svg;
                    rendered++;
                })
                /* Leave the raw text visible on a parse error, and keep going:
                 * one bad diagram must not swallow the ones after it. */
                .catch(function () { /* raw source stays */ })
                .then(function () { return step(i + 1); });
        };

        return step(0);
    }

    var api = { renderMermaid: renderMermaid };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewMermaid = api;
    }
})(typeof window !== "undefined" ? window : this);
