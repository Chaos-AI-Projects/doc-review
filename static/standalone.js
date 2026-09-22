/* doc-review — standalone presentation boot (MS-619 slice 2).
 *
 * Turns a markdown file into a deck with no server behind it.  This is the
 * whole page on ChaosEternal.github.io: no comments, no navigator, no review
 * DOM to return to.  The review app's own boot stays in templates/view.html,
 * because it has a server to fall back on and this does not.
 *
 * Two things are deliberately NOT here.  The deck DOM is deck_dom.js and the
 * key/control vocabulary is nav_logic.js, both shared with the review app: a
 * second copy of either would let a slide render or a keypress behave
 * differently in the two places, which is the drift view_specs.py exists to
 * prevent.  And the Pyodide CDN import stays in the calling page, handed in as
 * `createRuntime`, exactly as the mermaid and Pyodide imports stay in
 * view.html.  That is what lets test_standalone.js drive the entire load path
 * under Node with no network.
 *
 * The resources are read as RAW TEXT from urls relative to the page.  The
 * review app reaches the same two modules through /spike and /py, which wrap
 * the source in JSON; a published page has neither route, only files sitting
 * next to it.
 */
(function (root) {
    "use strict";

    /* Chaos took the CDN-boot risk knowingly (email 2026-09-22), so a slow
     * network has to read as slow rather than as broken.  Every state lands on
     * one element as `data-state` plus text: the page has no other chrome to
     * put a spinner in.  The TEXT is what the viewer reads -- style.css has no
     * rule for this page's chrome yet, and slice 3 chooses the subset it
     * ships -- so `data-state` is here for the tests and for that later
     * styling, and must never be the only thing a state changes. */
    function setStatus(el, state, text) {
        if (!el) return;
        el.setAttribute("data-state", state);
        el.textContent = text;
    }

    /* Which markdown file ?talk= asks for, or `fallback` when it asks for
     * something this page will not fetch.
     *
     * REJECT, never repair.  Stripping the offending characters instead turns
     * "../../etc/passwd" into "....etcpasswd" and then fetches that, so a
     * viewer who followed a tampered link gets a 404 for a name nobody wrote
     * rather than the talk they expected.  A name is acceptable only if it
     * survives the filter unchanged, which also keeps the rule readable as one
     * question: is this a plain file sitting next to the page?
     *
     * A bare name and nothing else: no slash, so no other directory; no colon,
     * so no other origin; and the published page fetches only its own
     * neighbours.  The first character must be alphanumeric, which is what
     * rules out ".." on its own -- a character class alone accepts it, and it
     * resolves to the directory above the deck. */
    var TALK_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

    function safeTalkName(param, fallback) {
        return TALK_NAME.test(param || "") ? param : fallback;
    }

    /* What this page can carry out, and therefore what deck_dom.js builds a
     * control for.  "exit" is missing on purpose: the review app's Esc uncovers
     * the review DOM the deck is covering, and here there is nothing behind the
     * deck to uncover.  Shipping the third button anyway gave a published page
     * a ✕ labelled "Exit presentation" that did nothing, which on a phone --
     * where the controls are the only evidence the page is alive -- is
     * indistinguishable from a deck that has stopped responding.
     *
     * The other half of that dead affordance was a fullscreen release, since
     * removed: nothing on this page ever requests fullscreen, and it cannot,
     * because requestFullscreen needs a user gesture that the mount (three
     * fetches and a Pyodide boot later) does not have.  Browsers already leave
     * fullscreen on Esc without being asked. */
    var DECK_ACTIONS = ["prev", "next"];

    /* The deck, its navigation and its status line, over one mount element. */
    function createDeckApp(env) {
        var doc = env.doc;
        var deckDom = env.deckDom;
        var navLogic = env.navLogic;
        var mount = env.mount;
        var statusEl = env.statusEl;

        var deckEl = null;
        var index = 0;

        function sections() {
            return deckEl ? deckEl.querySelectorAll("section.slide") : [];
        }

        function showSlide(next) {
            var list = sections();
            index = navLogic.clampSlide(next, list.length);
            for (var i = 0; i < list.length; i++) {
                list[i].hidden = i !== index;
            }
        }

        /* The ONE place an action is carried out, as in app.js: keys and
         * on-screen controls funnel through here so a tap and a keypress
         * cannot drift into disagreeing about what "next" does.
         *
         * Returns whether the deck acted.  That answer is the page's only way
         * to know which keystrokes are its own, and the page needs it: an
         * unconditional preventDefault in the listener swallows Ctrl-F and
         * every other browser shortcut for the length of the talk.  Deciding
         * it in the listener instead would put a second copy of nav_logic's
         * key vocabulary in an inline script no test can reach. */
        function handleAction(action) {
            if (action === "next") { showSlide(index + 1); return true; }
            if (action === "prev") { showSlide(index - 1); return true; }
            /* Anything else is inert, "exit" included: Esc KEEPS the deck,
             * because there is nothing behind the slides to go back to and
             * unmounting would leave a blank tab mid-talk.  A trailing `else`
             * would instead make Esc, or any action later added to nav_logic,
             * silently navigate backwards. */
            return false;
        }

        function render(specs) {
            deckEl = deckDom.buildDeck(doc, specs, function (name) {
                handleAction(navLogic.presentationControlAction(name));
            }, DECK_ACTIONS);
            mount.appendChild(deckEl);
            showSlide(0);
            if (deckEl.focus) deckEl.focus();  // so the keys reach the deck
            return deckEl;
        }

        return {
            render: render,
            showSlide: showSlide,
            slideIndex: function () { return index; },
            handleAction: handleAction,
            handleKey: function (key) {
                return handleAction(navLogic.presentationAction(key));
            },
            setStatus: function (state, text) { setStatus(statusEl, state, text); },
        };
    }

    /* Load everything the deck needs and mount it.  Resolves true when a deck
     * is on screen and false when the page said why it is not, so the caller
     * never has to read the DOM to find out. It does not reject: a published
     * page has no console anybody is watching, so every failure has to become
     * visible text.
     *
     * opts.urls    { renderer, viewSpecs, markdown } -- relative to the page
     * opts.fetchText(url) -> Promise<string>
     * opts.createRuntime({renderer, viewSpecs}) -> Promise<runtime>
     *   runtime.renderBlocks(source)
     *   runtime.presentationSpecs(blocks, commentsByBlock, source)
     */
    function boot(opts) {
        var app = opts.app;
        var urls = opts.urls;
        var fetchText = opts.fetchText;

        app.setStatus("loading", "Loading the renderer\u2026");

        return Promise.all([
            fetchText(urls.renderer),
            fetchText(urls.viewSpecs),
            fetchText(urls.markdown),
        ]).then(function (sources) {
            app.setStatus("loading", "Starting the renderer\u2026");
            return opts.createRuntime({
                renderer: sources[0],
                viewSpecs: sources[1],
            }).then(function (runtime) {
                var markdown = sources[2];
                var blocks = runtime.renderBlocks(markdown);
                /* An empty comment map, not null: the same builder serves the
                 * review app, where the map carries the comments a block has.
                 * A published deck has none, and that is a fact about this
                 * page rather than a missing argument. */
                var specs = runtime.presentationSpecs(blocks, {}, markdown);
                if (!specs || !specs.available) {
                    /* Say which condition failed.  `available` needs BOTH a
                     * `marp: true` front-matter directive and at least one
                     * slide left after it, and a bare "cannot present this"
                     * sends the author looking at the wrong one. */
                    app.setStatus("unavailable",
                        "Nothing to present: the markdown needs a "
                        + "`marp: true` front-matter directive and at least "
                        + "one slide.");
                    return false;
                }
                app.render(specs);
                app.setStatus("ready", "");
                return true;
            });
        }).catch(function (err) {
            app.setStatus("error",
                "Could not load the presentation: " + (err && err.message ? err.message : err));
            return false;
        });
    }

    /* The ids standalone.html gives its two elements.  They live here because
     * bootPage is what looks them up; test_server.py reads them back out of
     * this file and checks the page still defines them. */
    var MOUNT_ID = "deck-mount";
    var STATUS_ID = "deck-status";

    /* Everything the page used to do in its own <script> tag: find the two
     * elements, build the app, wire the keyboard, read ?talk=, turn the mount's
     * dataset into urls and fetch each resource as raw text.
     *
     * None of that could be driven from Node while it sat inline, and four
     * separate mutations of it survived the entire suite -- including a
     * fetchText that read `(await resp.json()).source`, which is the review
     * app's /py and /spike shape and the exact regression this module exists to
     * prevent.  Substring assertions against the page's source pinned the
     * spelling of those lines without ever executing them.
     *
     * What stays in the page is the one thing Node cannot run: the Pyodide CDN
     * import, handed in as `createRuntime`, exactly as view.html keeps its own.
     *
     * env.doc            document
     * env.deckDom        window.docReviewDeck
     * env.navLogic       window.docReviewNavLogic
     * env.fetch(url)     -> Promise<Response>
     * env.search         location.search
     * env.createRuntime  as boot()'s opts.createRuntime
     */
    function bootPage(env) {
        var doc = env.doc;
        var mount = doc.getElementById(MOUNT_ID);
        var statusEl = doc.getElementById(STATUS_ID);

        var app = createDeckApp({
            doc: doc,
            deckDom: env.deckDom,
            navLogic: env.navLogic,
            mount: mount,
            statusEl: statusEl,
        });

        /* The raw key goes to the tested dispatcher, which answers whether the
         * deck acted, and only then is the keystroke claimed.  Reading the key
         * vocabulary here would be a second copy of nav_logic; claiming the
         * keystroke unconditionally would swallow Ctrl-F and every other
         * browser shortcut for the length of the talk. */
        doc.addEventListener("keydown", function (evt) {
            if (app.handleKey(evt.key)) evt.preventDefault();
        });

        /* Raw text, not resp.json(): these are files sitting next to the page.
         * fetch() also resolves for a 404, so `ok` is what turns a missing file
         * into the visible error rather than an empty deck. */
        function fetchText(url) {
            return env.fetch(url).then(function (resp) {
                if (!resp.ok) throw new Error(resp.status + " " + url);
                return resp.text();
            });
        }

        var params = new URLSearchParams(env.search || "");

        return boot({
            app: app,
            urls: {
                renderer: mount.dataset.rendererUrl,
                viewSpecs: mount.dataset.viewSpecsUrl,
                /* A talk name only: a url here would let a link point the page
                 * at any origin.  Query string first, dataset second -- the
                 * other order ignores ?talk= and still passes a test that only
                 * looks for the call. */
                markdown: safeTalkName(params.get("talk"), mount.dataset.markdownUrl),
            },
            fetchText: fetchText,
            createRuntime: env.createRuntime,
        });
    }

    var api = {
        createDeckApp: createDeckApp,
        boot: boot,
        bootPage: bootPage,
        safeTalkName: safeTalkName,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = api;
    } else {
        root.docReviewStandalone = api;
    }
})(typeof window !== "undefined" ? window : this);
