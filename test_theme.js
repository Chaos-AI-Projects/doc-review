#!/usr/bin/env node
/**
 * Behavioral test for the light/dark theme switch (DR-8).
 *
 * Loads the *real* module the browser runs (static/theme.js), following
 * test_mermaid_init.js, and drives install() against a fake window: a
 * documentElement, a localStorage and a matchMedia, each just deep enough to
 * be wrong in the ways the real ones can be.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

var path = require("path");
var theme = require(path.join(__dirname, "static", "theme.js"));

var failures = [];

function assert(label, actual, expected) {
    if (actual !== expected) {
        failures.push(label + ": expected " + expected + ", got " + actual);
    }
}

/* ── Fakes ── */

function element() {
    return {
        attributes: {},
        listeners: {},
        textContent: "",
        getAttribute: function (n) {
            return Object.prototype.hasOwnProperty.call(this.attributes, n)
                ? this.attributes[n] : null;
        },
        setAttribute: function (n, v) { this.attributes[n] = String(v); },
        addEventListener: function (type, fn) { this.listeners[type] = fn; },
    };
}

function storage(initial) {
    var data = initial ? { "doc-review-theme": initial } : {};
    return {
        data: data,
        getItem: function (k) { return Object.prototype.hasOwnProperty.call(data, k) ? data[k] : null; },
        setItem: function (k, v) { data[k] = String(v); },
    };
}

// Private browsing in some engines: the property exists and every call throws.
var throwingStorage = {
    getItem: function () { throw new Error("SecurityError"); },
    setItem: function () { throw new Error("QuotaExceededError"); },
};

function media(dark) {
    return {
        matches: dark,
        listener: null,
        addEventListener: function (type, fn) { if (type === "change") this.listener = fn; },
        flip: function (to) { this.matches = to; if (this.listener) this.listener({ matches: to }); },
    };
}

function fakeWindow(opts) {
    var root = element();
    var button = element();
    var docListeners = {};
    var mq = media(!!opts.prefersDark);
    return {
        mq: mq,
        root: root,
        button: button,
        localStorage: opts.storage,
        matchMedia: function () { return mq; },
        document: {
            documentElement: root,
            readyState: opts.readyState || "loading",
            getElementById: function (id) { return id === "theme-toggle" ? button : null; },
            addEventListener: function (type, fn) { docListeners[type] = fn; },
            fire: function (type) { if (docListeners[type]) docListeners[type](); },
        },
    };
}

/* ── Pure functions ── */

assert("resolve: a stored choice beats the OS", theme.resolveTheme("light", true), "light");
assert("resolve: a stored dark beats a light OS", theme.resolveTheme("dark", false), "dark");
assert("resolve: no choice follows a dark OS", theme.resolveTheme(null, true), "dark");
assert("resolve: no choice follows a light OS", theme.resolveTheme(null, false), "light");
assert("resolve: junk in storage is ignored", theme.resolveTheme("purple", true), "dark");
assert("next: light -> dark", theme.nextTheme("light"), "dark");
assert("next: dark -> light", theme.nextTheme("dark"), "light");
assert("mermaid: dark page draws dark diagrams", theme.mermaidTheme("dark"), "dark");
assert("mermaid: light page keeps mermaid's default", theme.mermaidTheme("light"), "default");

/* ── install() ── */

// 1. First paint: the attribute is set before the DOM exists, so there is no
//    light flash on a dark OS.
(function () {
    var win = fakeWindow({ prefersDark: true, storage: storage() });
    var api = theme.install(win);
    assert("install: follows the OS before DOMContentLoaded", win.root.getAttribute("data-theme"), "dark");
    assert("install: current() reports it", api.current(), "dark");
    assert("install: following the OS writes nothing", win.localStorage.data["doc-review-theme"], undefined);
})();

// 2. A stored choice wins over the OS.
(function () {
    var win = fakeWindow({ prefersDark: true, storage: storage("light") });
    theme.install(win);
    assert("stored: light beats a dark OS", win.root.getAttribute("data-theme"), "light");
})();

// 3. The header button toggles, persists, and labels itself.
(function () {
    var win = fakeWindow({ prefersDark: false, storage: storage() });
    var api = theme.install(win);
    var seen = [];
    api.onChange(function (t) { seen.push(t); });
    win.document.fire("DOMContentLoaded");
    assert("button: says it switches to dark", win.button.getAttribute("aria-label"), "Switch to dark theme");
    win.button.listeners.click();
    assert("toggle: page is dark", win.root.getAttribute("data-theme"), "dark");
    assert("toggle: choice persisted", win.localStorage.data["doc-review-theme"], "dark");
    // A label that names the action, and no aria-pressed beside it: the two
    // together read as contradictory to a screen reader.
    assert("toggle: no aria-pressed", win.button.getAttribute("aria-pressed"), null);
    assert("toggle: button now offers light", win.button.getAttribute("aria-label"), "Switch to light theme");
    assert("toggle: listeners told once", seen.join(","), "dark");
    win.button.listeners.click();
    assert("toggle back: page is light", win.root.getAttribute("data-theme"), "light");
    assert("toggle back: listeners told again", seen.join(","), "dark,light");
})();

// 4. The script may run after the DOM is parsed; the button still binds.
(function () {
    var win = fakeWindow({ prefersDark: false, storage: storage(), readyState: "complete" });
    theme.install(win);
    assert("late install: button bound", typeof win.button.listeners.click, "function");
})();

// 5. An OS switch is followed live, until the user makes a choice.
(function () {
    var win = fakeWindow({ prefersDark: false, storage: storage() });
    var api = theme.install(win);
    var seen = [];
    api.onChange(function (t) { seen.push(t); });
    win.mq.flip(true);
    assert("os change: followed", win.root.getAttribute("data-theme"), "dark");
    assert("os change: listeners told", seen.join(","), "dark");
    api.set("light");
    win.mq.flip(false);
    win.mq.flip(true);
    assert("os change: ignored after a choice", win.root.getAttribute("data-theme"), "light");
})();

// 6. Storage that throws must not take the toggle down with it.
(function () {
    var win = fakeWindow({ prefersDark: true, storage: throwingStorage });
    var api = theme.install(win);
    assert("throwing storage: still follows the OS", api.current(), "dark");
    api.toggle();
    assert("throwing storage: toggle still works", api.current(), "light");
})();

// 7. No localStorage or matchMedia at all.
(function () {
    var win = fakeWindow({ storage: undefined });
    win.matchMedia = undefined;
    var api = theme.install(win);
    assert("bare window: light", api.current(), "light");
})();

if (failures.length) {
    console.error("FAILED (" + failures.length + "):");
    failures.forEach(function (f) { console.error("  - " + f); });
    process.exit(1);
}
console.log("All theme tests passed.");
