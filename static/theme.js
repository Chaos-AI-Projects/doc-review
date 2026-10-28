/* doc-review — light/dark theme switch (DR-8).
 *
 * Loaded as a plain blocking script in <head>, before style.css, so the
 * `data-theme` attribute is on <html> before first paint and a dark-OS reader
 * never sees a light flash.  The stylesheet does the rest: every colour is a
 * `light-dark()` variable, resolved by the root's `color-scheme`.
 *
 * With no stored choice the page follows prefers-color-scheme, live.  The
 * header button stores an explicit choice in localStorage, which then wins
 * over the OS until the reader clears it.
 *
 * Dependency-free and UMD-shaped like mermaid_init.js, so test_theme.js can
 * `require()` the same code the browser runs and drive install() against a
 * fake window.
 */
(function (root) {
    "use strict";

    var STORAGE_KEY = "doc-review-theme";

    function resolveTheme(stored, prefersDark) {
        if (stored === "dark" || stored === "light") return stored;
        return prefersDark ? "dark" : "light";
    }

    function nextTheme(current) {
        return current === "dark" ? "light" : "dark";
    }

    /* Mermaid's own theme names: "default" is its light one. */
    function mermaidTheme(theme) {
        return theme === "dark" ? "dark" : "default";
    }

    /* Storage can be absent, or present and throwing on every call (private
     * browsing in some engines).  Either way the toggle still works for this
     * page; it just does not persist. */
    function readStored(win) {
        try { return win.localStorage ? win.localStorage.getItem(STORAGE_KEY) : null; }
        catch (e) { return null; }
    }

    function writeStored(win, value) {
        try { if (win.localStorage) win.localStorage.setItem(STORAGE_KEY, value); }
        catch (e) { /* not persisted */ }
    }

    function install(win) {
        var doc = win.document;
        var el = doc.documentElement;
        var mq = typeof win.matchMedia === "function"
            ? win.matchMedia("(prefers-color-scheme: dark)") : null;
        var listeners = [];
        var button = null;

        function current() { return el.getAttribute("data-theme"); }

        function label() {
            if (!button) return;
            var dark = current() === "dark";
            button.textContent = dark ? "☀" : "☾";
            button.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
            button.setAttribute("title", dark ? "Switch to light theme" : "Switch to dark theme");
        }

        function apply(theme) {
            if (current() === theme) return;
            el.setAttribute("data-theme", theme);
            label();
            listeners.forEach(function (fn) { fn(theme); });
        }

        function set(theme) {
            writeStored(win, theme);
            apply(theme);
        }

        function toggle() { set(nextTheme(current())); }

        function bind() {
            button = doc.getElementById("theme-toggle");
            if (!button) return;
            button.addEventListener("click", toggle);
            label();
        }

        el.setAttribute("data-theme", resolveTheme(readStored(win), mq && mq.matches));

        if (mq && typeof mq.addEventListener === "function") {
            mq.addEventListener("change", function (e) {
                if (readStored(win) === null) apply(e.matches ? "dark" : "light");
            });
        }

        if (doc.readyState === "loading") {
            doc.addEventListener("DOMContentLoaded", bind);
        } else {
            bind();
        }

        return {
            current: current,
            set: set,
            toggle: toggle,
            mermaidTheme: function () { return mermaidTheme(current()); },
            onChange: function (fn) { listeners.push(fn); },
        };
    }

    var api = {
        resolveTheme: resolveTheme,
        nextTheme: nextTheme,
        mermaidTheme: mermaidTheme,
        install: install,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewTheme = install(root);
    }
})(typeof window !== "undefined" ? window : this);
