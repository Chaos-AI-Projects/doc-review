#!/usr/bin/env node
/**
 * Behavioral test for the standalone presentation boot (MS-619 slice 2).
 *
 * Loads the *real* modules the browser runs -- static/standalone.js over
 * static/deck_dom.js and static/nav_logic.js -- following test_deck_dom.js.
 * The boot takes its fetcher, its Pyodide runtime factory and its `document`
 * as arguments, so the whole load path is exercised here without a browser and
 * without a network.
 *
 * What the seam is for: on ChaosEternal.github.io there is no server, so the
 * page reads renderer.py, view_specs.py and the talk markdown as plain files
 * next to itself.  The review app instead reaches them through /spike and /py,
 * which wrap the source in JSON.  A boot that assumed the JSON wrapper would
 * fail only once published, which is the one place nobody is watching a
 * console.
 *
 * Exit 0 = all pass, exit 1 = failure (message on stderr).
 */
"use strict";

var path = require("path");
var deckDom = require(path.join(__dirname, "static", "deck_dom.js"));
var navLogic = require(path.join(__dirname, "static", "nav_logic.js"));
var standalone = require(path.join(__dirname, "static", "standalone.js"));

var failures = [];

function assert(label, actual, expected) {
    if (actual !== expected) {
        failures.push(label + ": expected " + JSON.stringify(expected) +
            ", got " + JSON.stringify(actual));
    }
}

function assertDeep(label, actual, expected) {
    var a = JSON.stringify(actual);
    var e = JSON.stringify(expected);
    if (a !== e) failures.push(label + ":\n      expected " + e + "\n      got      " + a);
}

/* ── A document stub deep enough for the builder, as in test_deck_dom.js ── */

function node(tag) {
    return {
        tag: tag,
        className: "",
        textContent: "",
        innerHTML: null,
        hidden: false,
        type: "",
        title: "",
        tabIndex: undefined,
        children: [],
        attrs: {},
        listeners: {},
        appendChild: function (child) { this.children.push(child); return child; },
        removeChild: function (child) {
            this.children = this.children.filter(function (c) { return c !== child; });
            return child;
        },
        setAttribute: function (name, value) { this.attrs[name] = String(value); },
        getAttribute: function (name) {
            return Object.prototype.hasOwnProperty.call(this.attrs, name)
                ? this.attrs[name] : null;
        },
        addEventListener: function (type, fn) {
            (this.listeners[type] = this.listeners[type] || []).push(fn);
        },
        click: function () {
            var self = this;
            (this.listeners.click || []).forEach(function (fn) {
                fn.call(self, { preventDefault: function () {}, stopPropagation: function () {} });
            });
        },
        focus: function () { this.focused = true; },
        querySelectorAll: function (sel) {
            var out = [];
            (function walk(n) {
                n.children.forEach(function (c) {
                    if (matches(c, sel)) out.push(c);
                    walk(c);
                });
            })(this);
            return out;
        },
    };
}

function matches(n, sel) {
    var parts = sel.split(".");
    var tag = parts.shift();
    if (tag && n.tag !== tag) return false;
    return parts.every(function (cls) {
        return (" " + n.className + " ").indexOf(" " + cls + " ") !== -1;
    });
}

function makeDoc() {
    return { createElement: node };
}

/* A document bootPage can boot over: the two elements it looks up by id, the
 * mount's dataset, and a keydown listener the test can fire by hand. */
function makePageDoc(dataset) {
    var doc = makeDoc();
    var mount = node("div");
    mount.dataset = dataset;
    var status = node("div");
    var byId = { "deck-mount": mount, "deck-status": status };
    doc.lookups = [];
    doc.listeners = {};
    doc.getElementById = function (id) {
        doc.lookups.push(id);
        return byId[id] || null;
    };
    doc.addEventListener = function (type, fn) {
        (doc.listeners[type] = doc.listeners[type] || []).push(fn);
    };
    doc.mount = mount;
    doc.status = status;
    return doc;
}

var PAGE_DATASET = {
    rendererUrl: "renderer.py",
    viewSpecsUrl: "view_specs.py",
    markdownUrl: "talk.md",
};

/* ── Fixtures ── */

var MARKDOWN = "---\nmarp: true\n---\n\n# One\n\n---\n\n# Two\n";

function specsFixture(available) {
    return {
        available: available !== false,
        theme: "default",
        paginate: false,
        slides: [
            { index: 0, number: 1, layout: "default", rows: [{ html: "<h1>One</h1>", startLine: 4, endLine: 4 }] },
            { index: 1, number: 2, layout: "default", rows: [{ html: "<h1>Two</h1>", startLine: 8, endLine: 8 }] },
        ],
    };
}

/* A fetcher over a fixed url->text map.  It returns STRINGS, so a boot that
 * reached for response.json() -- the review app's shape -- cannot pass. */
function fakeFetchText(map, calls) {
    return function (url) {
        calls.push(url);
        if (!Object.prototype.hasOwnProperty.call(map, url)) {
            return Promise.reject(new Error("404 " + url));
        }
        return Promise.resolve(map[url]);
    };
}

/* A `fetch` over a fixed url->text map, returning Response-SHAPED objects.
 *
 * json() answers the review app's own {"source": ...} wrapper rather than
 * throwing, which is what makes this a real test: a fetcher that reached for
 * resp.json() gets a plausible object back and fails only where the sources
 * reach the runtime.  404s RESOLVE, the way fetch() does, so the `ok` check is
 * the only thing standing between a missing file and an empty deck. */
function fakeFetch(map, calls) {
    return function (url) {
        calls.push(url);
        var has = Object.prototype.hasOwnProperty.call(map, url);
        return Promise.resolve({
            ok: has,
            status: has ? 200 : 404,
            text: function () { return Promise.resolve(map[url]); },
            json: function () { return Promise.resolve({ source: "WRAPPED " + url }); },
        });
    };
}

function fakeRuntime(log, specs) {
    return function (sources) {
        log.push(["createRuntime", sources.renderer, sources.viewSpecs]);
        return Promise.resolve({
            renderBlocks: function (src) {
                log.push(["renderBlocks", src]);
                return [{ start_line: 4, html: "<h1>One</h1>" }];
            },
            presentationSpecs: function (blocks, comments, src) {
                log.push(["presentationSpecs", blocks, comments, src]);
                return specs;
            },
        });
    };
}

function harness(opts) {
    opts = opts || {};
    var doc = makeDoc();
    var mount = node("div");
    var status = node("div");
    var calls = [];
    var log = [];
    var urls = {
        renderer: "renderer.py",
        viewSpecs: "view_specs.py",
        markdown: "talk.md",
    };
    var map = opts.map || {
        "renderer.py": "# renderer source",
        "view_specs.py": "# view_specs source",
        "talk.md": MARKDOWN,
    };
    var app = standalone.createDeckApp({
        doc: doc,
        deckDom: deckDom,
        navLogic: navLogic,
        mount: mount,
        statusEl: status,
    });
    return {
        doc: doc, mount: mount, status: status, calls: calls, log: log, app: app,
        boot: function () {
            return standalone.boot({
                app: app,
                urls: urls,
                fetchText: opts.fetchText || fakeFetchText(map, calls),
                createRuntime: opts.createRuntime
                    || fakeRuntime(log, opts.specs || specsFixture()),
            });
        },
    };
}

function slides(h) {
    return h.mount.querySelectorAll("section.slide");
}

/* ── Cases ── */

var cases = [];

// 1. The three resources are read as raw text, from the urls given.
cases.push(function () {
    var h = harness();
    return h.boot().then(function () {
        assertDeep("case1 urls fetched", h.calls.sort(),
            ["renderer.py", "talk.md", "view_specs.py"]);
        assertDeep("case1 runtime got both python sources",
            h.log[0], ["createRuntime", "# renderer source", "# view_specs source"]);
    });
});

// 2. presentation_specs is called with an EMPTY comment map and the raw source.
//    There are no comments on a published deck, and passing null instead would
//    make the slide rows disagree with the review app's.
cases.push(function () {
    var h = harness();
    return h.boot().then(function () {
        var call = h.log.filter(function (e) { return e[0] === "presentationSpecs"; })[0];
        assertDeep("case2 comment map is empty, not null", call[2], {});
        assert("case2 source passed through", call[3], MARKDOWN);
        assertDeep("case2 blocks come from renderBlocks", call[1],
            [{ start_line: 4, html: "<h1>One</h1>" }]);
    });
});

// 3. The deck is built by the shared builder and mounted, first slide only.
cases.push(function () {
    var h = harness();
    return h.boot().then(function (ok) {
        assert("case3 boot reports success", ok, true);
        var decks = h.mount.querySelectorAll("div.presentation");
        assert("case3 one deck mounted", decks.length, 1);
        // Without focus the arrow keys go to the body and the deck ignores
        // them until the viewer happens to click it.
        assert("case3 the mounted deck takes focus", decks[0].focused, true);
        var s = slides(h);
        assert("case3 slide count", s.length, 2);
        assert("case3 slide 1 visible", s[0].hidden, false);
        assert("case3 slide 2 hidden", s[1].hidden, true);
        assert("case3 controls present",
            h.mount.querySelectorAll("div.presentation-controls").length, 1);
    });
});

// 3b. The control bar carries the actions this page can actually carry out,
//     and no others.  A published deck has nothing behind it to exit to, so a
//     third button labelled "Exit presentation" would be a dead control -- on a
//     phone, indistinguishable from a page that has stopped responding.
cases.push(function () {
    var h = harness();
    return h.boot().then(function () {
        assertDeep("case3b only the navigation controls are built",
            h.mount.querySelectorAll("button.presentation-control")
                .map(function (b) { return b.getAttribute("data-action"); }),
            ["prev", "next"]);
    });
});

// 4. The status element is the promised loading state: it says something while
//    Pyodide boots and stops saying it once the deck is up.  The two loading
//    messages are distinct, because fetching three small files and starting
//    Pyodide take wildly different times and a viewer staring at one frozen
//    line cannot tell a slow boot from a dead page.
cases.push(function () {
    var atRuntimeStart = null;
    var runtimeLog = [];
    var h;
    h = harness({
        createRuntime: function (sources) {
            // Read synchronously, inside the call: this is the only moment the
            // second message is on screen.
            atRuntimeStart = {
                text: h.status.textContent,
                state: h.status.attrs["data-state"],
            };
            return fakeRuntime(runtimeLog, specsFixture())(sources);
        },
    });
    var p = h.boot();
    // Read both synchronously: the boot resolves before any .then() body runs,
    // so a deferred read would only ever see the final state.
    var duringText = h.status.textContent;
    var duringState = h.status.attrs["data-state"];
    return p.then(function () {
        assert("case4 loading state shown during boot",
            duringText.length > 0 && /load/i.test(duringText), true);
        assert("case4 loading state during boot", duringState, "loading");
        assert("case4 runtime start is its own message",
            atRuntimeStart.text !== duringText && /start/i.test(atRuntimeStart.text),
            true);
        assert("case4 runtime start is still a loading state",
            atRuntimeStart.state, "loading");
        assert("case4 ready state after boot", h.status.attrs["data-state"], "ready");
    });
});

// 5. A failed fetch leaves the error on screen rather than a blank page, and
//    mounts no deck.  This is the CDN-stall case Chaos accepted knowingly.
cases.push(function () {
    var h = harness({ map: { "renderer.py": "# r" } });
    return h.boot().then(function (ok) {
        assert("case5 boot reports failure", ok, false);
        assert("case5 error state", h.status.attrs["data-state"], "error");
        assert("case5 message mentions the failure",
            /404|could not/i.test(h.status.textContent), true);
        assert("case5 nothing mounted", h.mount.children.length, 0);
    });
});

// 6. A document that does not declare itself a deck is a stated condition, not
//    an empty page: `available` is false for markdown without `marp: true`.
cases.push(function () {
    var h = harness({ specs: specsFixture(false) });
    return h.boot().then(function (ok) {
        assert("case6 boot reports failure", ok, false);
        assert("case6 unavailable state", h.status.attrs["data-state"], "unavailable");
        assert("case6 message names the directive",
            /marp/i.test(h.status.textContent), true);
        assert("case6 nothing mounted", h.mount.children.length, 0);
    });
});

// 7. Keys and control presses share one dispatcher and clamp at both ends,
//    through the same nav_logic the review app uses.
cases.push(function () {
    var h = harness();
    return h.boot().then(function () {
        h.app.handleKey("ArrowRight");
        assert("case7 next", h.app.slideIndex(), 1);
        h.app.handleKey("ArrowRight");
        assert("case7 clamps at the last slide", h.app.slideIndex(), 1);
        h.app.handleKey("ArrowLeft");
        assert("case7 prev", h.app.slideIndex(), 0);
        h.app.handleKey("ArrowLeft");
        assert("case7 clamps at the first slide", h.app.slideIndex(), 0);
        h.app.handleKey("q");
        assert("case7 an unmapped key is inert", h.app.slideIndex(), 0);
        var next = h.mount.querySelectorAll("button.control-next")[0];
        next.click();
        assert("case7 the on-screen control advances too", h.app.slideIndex(), 1);
        // Away from slide 0, where a stray `else` branch would show: at index 0
        // a backwards move clamps back to 0 and looks inert whatever happens.
        h.app.handleKey("q");
        assert("case7 an unmapped key is inert mid-deck too", h.app.slideIndex(), 1);
        var s = slides(h);
        assert("case7 visibility follows the index", s[1].hidden, false);
        assert("case7 previous slide hidden", s[0].hidden, true);
    });
});

// 8. Esc keeps the deck.  In the review app Esc returns to a hidden review DOM;
//    on a published page there is nothing behind the deck, so tearing it down
//    would leave a blank tab mid-talk with no way back.  Esc is therefore a
//    no-op here, and the deck stays on the slide it was on.
cases.push(function () {
    var doc = makeDoc();
    var mount = node("div");
    var app = standalone.createDeckApp({
        doc: doc,
        deckDom: deckDom,
        navLogic: navLogic,
        mount: mount,
        statusEl: node("div"),
    });
    app.render(specsFixture());
    app.handleKey("ArrowRight");
    app.handleKey("Escape");
    assert("case8 deck still mounted", mount.querySelectorAll("div.presentation").length, 1);
    assert("case8 slide index unchanged", app.slideIndex(), 1);
    var s = mount.querySelectorAll("section.slide");
    assert("case8 the slide on screen is unchanged", s[1].hidden, false);
    return Promise.resolve();
});

// 9. The browser entry point is published on `window`, the way deck_dom.js and
//    nav_logic.js are.  static/standalone.html loads it as a plain script.
cases.push(function () {
    var fs = require("fs");
    var vm = require("vm");
    var src = fs.readFileSync(path.join(__dirname, "static", "standalone.js"), "utf8");
    var sandbox = { window: {}, console: console };
    sandbox.self = sandbox;
    vm.runInNewContext(src, sandbox);
    var api = sandbox.window.docReviewStandalone;
    assert("case9 published on window", typeof api, "object");
    assert("case9 boot exported", typeof (api || {}).boot, "function");
    assert("case9 createDeckApp exported", typeof (api || {}).createDeckApp, "function");
    assert("case9 safeTalkName exported", typeof (api || {}).safeTalkName, "function");
    assert("case9 bootPage exported", typeof (api || {}).bootPage, "function");
    return Promise.resolve();
});

// 10. ?talk= names a file next to the page, and an unacceptable name is
//     REJECTED rather than repaired.  Repairing it silently is the trap: a
//     stripping filter turns "../../etc/passwd" into "....etcpasswd" and then
//     fetches that, so a viewer who followed a tampered link sees a 404 for a
//     name they never wrote instead of the talk they asked for -- and a filter
//     whose output is never compared to its input can be deleted outright with
//     every test still green, which is how this page shipped once already.
cases.push(function () {
    var safe = standalone.safeTalkName;
    assert("case10 a plain name is kept", safe("intro.md", "talk.md"), "intro.md");
    assert("case10 dots and dashes are kept",
        safe("2026-09-22_notes.md", "talk.md"), "2026-09-22_notes.md");
    assert("case10 a traversal is rejected, not stripped",
        safe("../../etc/passwd", "talk.md"), "talk.md");
    assert("case10 an absolute url is rejected",
        safe("https://evil.example/x.md", "talk.md"), "talk.md");
    assert("case10 a server route is rejected", safe("/py/talk.md", "talk.md"), "talk.md");
    // A slash ANYWHERE, not just leading.  Every other rejection here is
    // already refused by the leading-character anchor, so without this case
    // adding "/" back to the character class passes the whole suite -- and
    // then "x/../../py/view_specs.py" reaches the route the item forbids.
    assert("case10 an interior slash is rejected",
        safe("x/../../py/view_specs.py", "talk.md"), "talk.md");
    assert("case10 a subdirectory is rejected", safe("talks/intro.md", "talk.md"), "talk.md");
    // A backslash as well as a slash.  Browsers normalise "\" to "/" when
    // resolving an http(s) url, so this traversal leaves the deck's directory
    // exactly as the slash form does -- and every other case here is already
    // refused by something else, so widening the class to accept it passes.
    assert("case10 a backslash traversal is rejected",
        safe("a\\..\\..\\py\\view_specs.py", "talk.md"), "talk.md");
    // A character class alone accepts this one, and it resolves to the
    // directory above the deck.
    assert("case10 a bare parent reference is rejected", safe("..", "talk.md"), "talk.md");
    assert("case10 a missing param falls back", safe(null, "talk.md"), "talk.md");
    assert("case10 an empty param falls back", safe("", "talk.md"), "talk.md");
    return Promise.resolve();
});

// 11. Both dispatchers report whether they acted, and the page gates
//     preventDefault on that answer.  Without the return value the listener has
//     to decide for itself which keys are the deck's, which is a second copy of
//     nav_logic's vocabulary living in an untested inline script -- and it
//     swallows every key the deck does not use, so Ctrl-F and tab-switching
//     stop working mid-talk.
cases.push(function () {
    var h = harness();
    return h.boot().then(function () {
        assert("case11 a handled key reports true", h.app.handleKey("ArrowRight"), true);
        assert("case11 an unmapped key reports false", h.app.handleKey("q"), false);
        assert("case11 Escape reports false", h.app.handleKey("Escape"), false);
        assert("case11 a handled action reports true", h.app.handleAction("prev"), true);
        assert("case11 an absent action reports false", h.app.handleAction(null), false);
        assert("case11 an unhonoured action reports false", h.app.handleAction("exit"), false);
    });
});

/* ── bootPage: the page's own boot, which used to be an inline script ──
 *
 * These four cases exist because four mutations of that inline script survived
 * the entire suite while it lived in standalone.html: reading resp.json()
 * instead of resp.text(), swapping the renderer and view_specs urls, swapping
 * safeTalkName's two arguments, and calling preventDefault unconditionally.
 * Each was pinned only by a substring assertion in test_server.py, which
 * checks the spelling of a line without ever running it.
 */

function bootThePage(opts) {
    opts = opts || {};
    var doc = makePageDoc(opts.dataset || PAGE_DATASET);
    var calls = [];
    var log = [];
    var promise = standalone.bootPage({
        doc: doc,
        deckDom: deckDom,
        navLogic: navLogic,
        fetch: fakeFetch(opts.map || {
            "renderer.py": "# renderer source",
            "view_specs.py": "# view_specs source",
            "talk.md": MARKDOWN,
        }, calls),
        search: opts.search || "",
        createRuntime: fakeRuntime(log, opts.specs || specsFixture()),
    });
    return promise.then(function (ok) {
        return { ok: ok, doc: doc, calls: calls, log: log };
    });
}

// 12. The page boots end to end from its own markup: both elements found by
//     id, the dataset urls landing in the slots that match them, and every
//     resource arriving as the file's TEXT.
cases.push(function () {
    return bootThePage().then(function (r) {
        assert("case12 boot reports success", r.ok, true);
        assertDeep("case12 both elements are found by id", r.doc.lookups.slice().sort(),
            ["deck-mount", "deck-status"]);
        // Swap the two dataset urls and renderer.py's source arrives as
        // view_specs, which no url-level assertion notices.
        assertDeep("case12 each python source reaches its own slot",
            r.log[0], ["createRuntime", "# renderer source", "# view_specs source"]);
        assert("case12 the markdown is the file text, not a JSON wrapper",
            r.log.filter(function (e) { return e[0] === "renderBlocks"; })[0][1], MARKDOWN);
        assert("case12 the deck is mounted on the element carrying the mount id",
            r.doc.mount.querySelectorAll("div.presentation").length, 1);
        assert("case12 the status element is the one carrying the status id",
            r.doc.status.getAttribute("data-state"), "ready");
    });
});

// 13. ?talk= chooses the file, the dataset supplies the default, and a
//     tampered name is never fetched.  Swap safeTalkName's arguments and the
//     query string is ignored while the call itself still looks right.
cases.push(function () {
    var files = {
        "renderer.py": "# r", "view_specs.py": "# v",
        "talk.md": MARKDOWN, "intro.md": MARKDOWN,
    };
    return bootThePage({ search: "?talk=intro.md", map: files }).then(function (r) {
        assert("case13 ?talk= chooses the file", r.calls.indexOf("intro.md") !== -1, true);
        assert("case13 and the dataset default is left alone",
            r.calls.indexOf("talk.md"), -1);
        return bootThePage({ search: "?talk=../../etc/passwd", map: files });
    }).then(function (r) {
        assert("case13 a tampered name falls back to the dataset default",
            r.calls.indexOf("talk.md") !== -1, true);
        assert("case13 and is never fetched",
            r.calls.indexOf("../../etc/passwd"), -1);
        return bootThePage({ search: "", map: files });
    }).then(function (r) {
        assert("case13 no ?talk= reads the dataset default",
            r.calls.indexOf("talk.md") !== -1, true);
    });
});

// 14. The page's keyboard listener claims a keystroke only when the deck acted
//     on it.  An unconditional preventDefault kills Ctrl-F, tab-switching and
//     every other browser shortcut for the length of the talk, and it reads
//     identically in the source.
cases.push(function () {
    return bootThePage().then(function (r) {
        var listener = (r.doc.listeners.keydown || [])[0];
        assert("case14 the page listens for keys", typeof listener, "function");
        function press(key) {
            var claimed = false;
            listener({ key: key, preventDefault: function () { claimed = true; } });
            return claimed;
        }
        assert("case14 a key the deck acts on is claimed", press("ArrowRight"), true);
        var s = r.doc.mount.querySelectorAll("section.slide");
        assert("case14 and the deck advanced", s[1].hidden, false);
        assert("case14 an unmapped key is left to the browser", press("f"), false);
        assert("case14 Escape is left to the browser", press("Escape"), false);
        assert("case14 Escape kept the deck", s[1].hidden, false);
    });
});

// 15. A 404 on one of the three files is an error on screen.  fetch() RESOLVES
//     for a 404, so without the `ok` check the body is served to the renderer
//     and the viewer gets an empty deck with no idea why.
cases.push(function () {
    return bootThePage({ map: { "renderer.py": "# r" } }).then(function (r) {
        assert("case15 boot reports failure", r.ok, false);
        assert("case15 error state", r.doc.status.getAttribute("data-state"), "error");
        assert("case15 the status code is on screen", /404/.test(r.doc.status.textContent), true);
        assert("case15 nothing mounted", r.doc.mount.children.length, 0);
    });
});

/* ── The page's own <script type="module">, driven under Node ──
 *
 * standalone.html keeps two things standalone.js cannot: the Pyodide CDN
 * import and the call that hands everything else to bootPage().  That is a
 * small script, but it is still code, and while nothing executed it a swap of
 * the two FS.writeFile sources and a `search` that never reached the ?talk=
 * filter both passed the whole suite -- the same two defects the seam was
 * moved to kill, one layer further out.
 *
 * So read the script out of the page and run it, with the browser stubbed.
 * The body has no static import, and its one dynamic import -- the CDN bundle,
 * which is the only line Node genuinely cannot run -- is swapped for a stub,
 * so this reaches no network.  A syntax error in the page fails here too,
 * because the vm parses the real text.
 */
function runPageScript(stubs) {
    var fs = require("fs");
    var vm = require("vm");
    var html = fs.readFileSync(path.join(__dirname, "static", "standalone.html"), "utf8");
    var open = html.indexOf('<script type="module">');
    if (open === -1) throw new Error("standalone.html has no module script");
    var body = html.slice(html.indexOf(">", open) + 1);
    body = body.slice(0, body.indexOf("</script>"));

    var sandbox = {
        window: stubs.window,
        document: stubs.document,
        console: console,
        URLSearchParams: URLSearchParams,
        __cdnImport: stubs.cdnImport,
    };
    sandbox.self = sandbox;
    vm.runInNewContext(body.replace("import(", "__cdnImport("), sandbox);
    return sandbox;
}

/* The page's stubbed browser.  `bootPage` records the environment it was
 * handed, which is the whole contract between the page and the tested half. */
function pageStubs(search) {
    var seen = { env: null, fetched: [], cdn: [] };
    var doc = { marker: "the page's document" };
    var deckDom = { marker: "window.docReviewDeck" };
    var navLogic = { marker: "window.docReviewNavLogic" };
    var pyodide = {
        calls: [],
        loadPackage: function (name) { pyodide.calls.push(["loadPackage", name]); return Promise.resolve(); },
        pyimport: function (name) {
            pyodide.calls.push(["pyimport", name]);
            return {
                install: function (pkgs) {
                    pyodide.calls.push(["install", pkgs.slice()]);
                    return Promise.resolve();
                },
            };
        },
        FS: { writeFile: function (p, data) { pyodide.calls.push(["writeFile", p, data]); } },
        globals: { set: function (k, v) { pyodide.calls.push(["set", k, v]); } },
        runPython: function (code) {
            pyodide.calls.push(["runPython", code]);
            if (code.indexOf("render_markdown_blocks") !== -1) {
                return JSON.stringify([{ start_line: 4, html: "<h1>One</h1>" }]);
            }
            if (code.indexOf("presentation_specs") !== -1) {
                return JSON.stringify(specsFixture());
            }
            return undefined;
        },
    };
    return {
        seen: seen,
        doc: doc,
        deckDom: deckDom,
        navLogic: navLogic,
        pyodide: pyodide,
        document: doc,
        cdnImport: function (url) {
            seen.cdn.push(url);
            return Promise.resolve({
                loadPyodide: function (opts) {
                    pyodide.calls.push(["loadPyodide", opts.indexURL]);
                    return Promise.resolve(pyodide);
                },
            });
        },
        window: {
            docReviewStandalone: {
                bootPage: function (env) { seen.env = env; return Promise.resolve(true); },
            },
            docReviewDeck: deckDom,
            docReviewNavLogic: navLogic,
            location: { search: search || "" },
            fetch: function (url) { seen.fetched.push(url); return Promise.resolve({ url: url }); },
        },
    };
}

// 16. The page hands bootPage the browser, and nothing less.  Each of these is
//     an identity, not a shape: swap deckDom for navLogic, or pin `search` to
//     the empty string, and the page still looks right while the deck builds
//     from the wrong module or ignores ?talk= entirely.
cases.push(function () {
    var stubs = pageStubs("?talk=intro.md");
    runPageScript(stubs);
    var env = stubs.seen.env;
    assert("case16 the page boots through the tested entry point", env !== null, true);
    assert("case16 the real document is handed in", env.doc, stubs.doc);
    assert("case16 the deck builder is handed in", env.deckDom, stubs.deckDom);
    assert("case16 the key vocabulary is handed in", env.navLogic, stubs.navLogic);
    assert("case16 the query string reaches the talk filter", env.search, "?talk=intro.md");
    assert("case16 the Pyodide factory is handed in", typeof env.createRuntime, "function");
    // The fetcher must be the browser's, for the url it is given: a wrapper
    // that ignores its argument serves one file for all three.
    return env.fetch("renderer.py").then(function () {
        assertDeep("case16 the fetcher reaches the browser with its own url",
            stubs.seen.fetched, ["renderer.py"]);
    });
});

// 17. The Pyodide half writes each source to the module file that matches it,
//     and passes the spec builder its arguments in the order it declares them.
//     Both survived every earlier test, because nothing ran this script: the
//     writeFile swap makes view_specs.py import as renderer and the page dies
//     only once published, which is exactly the class of bug the seam exists
//     to stop.
cases.push(function () {
    var stubs = pageStubs("");
    runPageScript(stubs);
    var createRuntime = stubs.seen.env.createRuntime;
    return createRuntime({ renderer: "# renderer source", viewSpecs: "# view_specs source" })
        .then(function (runtime) {
            var calls = stubs.pyodide.calls;
            function of(kind) {
                return calls.filter(function (e) { return e[0] === kind; });
            }
            assertDeep("case17 each source is written to its own module file", of("writeFile"),
                [["writeFile", "/home/pyodide/renderer.py", "# renderer source"],
                 ["writeFile", "/home/pyodide/view_specs.py", "# view_specs source"]]);
            assert("case17 the runtime is pinned to the same url it imports",
                stubs.seen.cdn[0].indexOf("pyodide") !== -1
                    && of("loadPyodide")[0][1].indexOf("pyodide") !== -1, true);
            // renderer.py parses front matter through mdit-py-plugins, and
            // front matter is what declares a document presentable at all.
            assertDeep("case17 both markdown packages are installed",
                of("install")[0][1].sort(), ["markdown-it-py", "mdit-py-plugins"]);

            runtime.renderBlocks("SOURCE TEXT");
            var set = of("set").slice(-2);
            assertDeep("case17 the markdown and a null doc path reach the renderer",
                set, [["set", "_source", "SOURCE TEXT"], ["set", "_doc_path", null]]);

            runtime.presentationSpecs([{ start_line: 4 }], {}, "SOURCE TEXT");
            var arg = of("set").slice(-1)[0];
            assertDeep("case17 the spec builder's arguments keep their order",
                JSON.parse(arg[2]), [[{ start_line: 4 }], {}, "SOURCE TEXT"]);
        });
});

/* ── Run ── */

cases.reduce(function (chain, fn, i) {
    return chain.then(fn).catch(function (err) {
        failures.push("case" + (i + 1) + " threw: " + err.message);
    });
}, Promise.resolve()).then(function () {
    if (failures.length) {
        console.error("FAIL (" + failures.length + "):");
        failures.forEach(function (f) { console.error("  - " + f); });
        process.exit(1);
    }
    console.log("test_standalone.js: " + cases.length + " cases passed");
});
