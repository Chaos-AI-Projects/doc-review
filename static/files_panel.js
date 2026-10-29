/* doc-review — the Files button and its panel (DR-14).
 *
 * The file tree and filter open over the text from the Files button, so the
 * left column holds only the document's Contents.  Kept out of app.js, whose
 * one keydown listener is the presentation keyboard route; this file listens
 * for Escape on its own.  When a click or key closes the panel is decided by
 * docReviewNavLogic.filesPanelCloses, which test_spa_nav.js covers.
 */
(function () {
    "use strict";

    var toggle = document.getElementById("nav-toggle");
    var panel = document.getElementById("files-panel");
    var logic = window.docReviewNavLogic;
    if (!toggle || !panel || !logic) return;

    function setOpen(open) {
        panel.hidden = !open;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
        toggle.classList.toggle("active", open);
        // On a phone, focusing the filter raises the keyboard over the tree.
        if (open && !window.matchMedia("(max-width: 768px)").matches) {
            var filter = document.getElementById("nav-filter");
            if (filter) filter.focus();
        }
    }

    toggle.addEventListener("click", function () {
        setOpen(panel.hidden);
    });

    function closeOn(e) {
        if (panel.hidden) return;
        var t = e.target;
        var inPanel = !!(t && panel.contains(t));
        var record = {
            type: e.type,
            key: e.key || "",
            inPanel: inPanel,
            onToggle: !!(t && toggle.contains(t)),
            onLink: inPanel && !!(t.closest && t.closest("a.file-nav-link")),
        };
        if (!logic.filesPanelCloses(record)) return;
        setOpen(false);
        if (e.type === "keydown") toggle.focus();
    }

    document.addEventListener("click", closeOn);
    document.addEventListener("keydown", closeOn);
})();
