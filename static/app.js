/* doc-review — minimal JS for comment interaction (block-anchored) */

(function () {
    "use strict";

    var commentsData = JSON.parse(
        document.getElementById("comments-data").textContent
    );

    var sidebar = document.getElementById("sidebar-content");
    var formTpl = document.getElementById("comment-form-tpl");
    var isMobile = window.matchMedia("(max-width: 768px)").matches;
    var presenting = false;  // presentation mode is read-only (#452)

    /* ── File navigator toggle (mobile) ── */

    var navToggle = document.getElementById("nav-toggle");
    var fileNav = document.getElementById("file-nav");
    if (navToggle && fileNav) {
        navToggle.addEventListener("click", function () {
            fileNav.classList.toggle("open");
        });
    }

    /* ── Column hide/show toggles ── */

    var navColToggle = document.getElementById("nav-col-toggle");
    var commentsColToggle = document.getElementById("comments-col-toggle");
    var commentSidebar = document.getElementById("sidebar");

    if (navColToggle && fileNav) {
        navColToggle.classList.add("active");
        navColToggle.addEventListener("click", function () {
            fileNav.classList.toggle("col-hidden");
            navColToggle.classList.toggle("active");
        });
    }

    if (commentsColToggle && commentSidebar) {
        commentsColToggle.classList.add("active");
        commentsColToggle.addEventListener("click", function () {
            commentSidebar.classList.toggle("col-hidden");
            commentsColToggle.classList.toggle("active");
        });
    }

    /* ── File navigator: directory tree + filter ── */

    var allFiles = JSON.parse(
        document.getElementById("files-data").textContent
    );
    var currentPath = JSON.parse(
        document.getElementById("current-path-data").textContent
    );
    var navTreeEl = document.getElementById("file-nav-tree");
    var navFilterEl = document.getElementById("nav-filter");

    function buildTree(files) {
        var root = {};
        for (var i = 0; i < files.length; i++) {
            var parts = files[i].split("/");
            var node = root;
            for (var j = 0; j < parts.length; j++) {
                if (j === parts.length - 1) {
                    if (!node._files) node._files = [];
                    node._files.push({ name: parts[j], path: files[i] });
                } else {
                    if (!node[parts[j]]) node[parts[j]] = {};
                    node = node[parts[j]];
                }
            }
        }
        return root;
    }

    function renderTree(node, container, prefix) {
        var dirs = [];
        var key;
        for (key in node) {
            if (key !== "_files" && node.hasOwnProperty(key)) {
                dirs.push(key);
            }
        }
        dirs.sort();
        for (var d = 0; d < dirs.length; d++) {
            var dirName = dirs[d];
            var dirPath = prefix ? prefix + "/" + dirName : dirName;
            var details = document.createElement("details");
            details.className = "nav-dir";
            details.open = currentPath === dirPath ||
                currentPath.indexOf(dirPath + "/") === 0;
            var summary = document.createElement("summary");
            summary.className = "nav-dir-name";
            summary.textContent = dirName + "/";
            details.appendChild(summary);
            var inner = document.createElement("div");
            inner.className = "nav-dir-children";
            renderTree(node[dirName], inner, dirPath);
            details.appendChild(inner);
            container.appendChild(details);
        }
        var files = node._files || [];
        for (var f = 0; f < files.length; f++) {
            var a = document.createElement("a");
            a.href = "/view?path=" + encodeURIComponent(files[f].path);
            a.className = "file-nav-link";
            if (files[f].path === currentPath) a.className += " active";
            a.textContent = files[f].name;
            a.setAttribute("data-path", files[f].path);
            container.appendChild(a);
        }
    }

    function renderFilteredFlat(files) {
        for (var i = 0; i < files.length; i++) {
            var a = document.createElement("a");
            a.href = "/view?path=" + encodeURIComponent(files[i]);
            a.className = "file-nav-link";
            if (files[i] === currentPath) a.className += " active";
            a.textContent = files[i];
            a.setAttribute("data-path", files[i]);
            navTreeEl.appendChild(a);
        }
    }

    var tree = buildTree(allFiles);
    renderTree(tree, navTreeEl, "");

    if (navFilterEl) {
        navFilterEl.addEventListener("input", function () {
            var q = navFilterEl.value.toLowerCase();
            navTreeEl.innerHTML = "";
            if (q === "") {
                renderTree(buildTree(allFiles), navTreeEl, "");
            } else {
                var filtered = allFiles.filter(function (f) {
                    return f.toLowerCase().indexOf(q) !== -1;
                });
                renderFilteredFlat(filtered);
            }
        });
    }

    /* ── Build comment HTML ── */

    function escapeHtml(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function commentCardHtml(c) {
        var cls = "comment-card" + (c.resolved ? " resolved" : "");
        if (c.parent_id) cls += " reply";
        var html = '<div class="' + cls + '">';
        html += '<div class="comment-meta">';
        if (c.parent_id) {
            html += '<span class="reply-badge">reply</span> ';
        }
        html +=
            "<strong>" +
            escapeHtml(c.author) +
            "</strong> &middot; L" +
            c.line_start +
            (c.line_end !== c.line_start ? "-" + c.line_end : "") +
            " &middot; " +
            escapeHtml(c.created_at.slice(0, 16)) +
            (c.resolved ? " &middot; <em>resolved</em>" : "") +
            "</div>";
        html += '<div class="comment-body">' + escapeHtml(c.body) + "</div>";
        html += '<div class="comment-actions">';
        if (!c.resolved) {
            html +=
                '<form method="post" action="/comment/' +
                c.id +
                '/resolve" style="display:inline">' +
                '<input type="hidden" name="path" value="' +
                escapeHtml(getPath()) +
                '">' +
                '<button type="submit">Resolve</button></form>';
        } else {
            html +=
                '<form method="post" action="/comment/' +
                c.id +
                '/unresolve" style="display:inline">' +
                '<input type="hidden" name="path" value="' +
                escapeHtml(getPath()) +
                '">' +
                '<button type="submit">Unresolve</button></form>';
        }
        html += "</div>";
        html += "</div>";
        return html;
    }

    function getPath() {
        return new URLSearchParams(window.location.search).get("path") || "";
    }

    /* ── Show comments for a block in the sidebar ── */

    function showBlockComments(startLine, endLine) {
        var comments = commentsData[String(startLine)] || [];
        var label = startLine === endLine
            ? "Line " + startLine
            : "Lines " + startLine + "-" + endLine;
        var html = "<h3>" + label + "</h3>";
        for (var i = 0; i < comments.length; i++) {
            html += commentCardHtml(comments[i]);
        }

        var frag = formTpl.content.cloneNode(true);
        var form = frag.querySelector("form");
        form.querySelector('[name="line_start"]').value = startLine;
        form.querySelector('[name="line_end"]').value = endLine;
        var tmp = document.createElement("div");
        tmp.appendChild(frag);
        html += tmp.innerHTML;

        sidebar.innerHTML = html;

        var cancelBtn = sidebar.querySelector(".cancel-btn");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", function () {
                sidebar.innerHTML =
                    '<p class="sidebar-hint">Click a block to add a comment.</p>';
            });
        }

        document.querySelectorAll(".source-line.active").forEach(function (el) {
            el.classList.remove("active");
        });
        var row = document.getElementById("L" + startLine);
        if (row) row.classList.add("active");
    }

    /* ── Mobile: toggle inline comment panel under the block ── */

    function showBlockCommentsMobile(startLine, endLine) {
        document.querySelectorAll(".inline-comments").forEach(function (el) {
            el.remove();
        });

        var row = document.getElementById("L" + startLine);
        if (!row) return;

        var comments = commentsData[String(startLine)] || [];
        var html = "";
        for (var i = 0; i < comments.length; i++) {
            html += commentCardHtml(comments[i]);
        }
        var frag = formTpl.content.cloneNode(true);
        var form = frag.querySelector("form");
        form.querySelector('[name="line_start"]').value = startLine;
        form.querySelector('[name="line_end"]').value = endLine;
        var tmp = document.createElement("div");
        tmp.appendChild(frag);
        html += tmp.innerHTML;

        var panel = document.createElement("tr");
        panel.className = "inline-comments";
        panel.innerHTML = '<td colspan="3">' + html + "</td>";
        panel.style.display = "table-row";
        row.after(panel);

        var cancelBtn = panel.querySelector(".cancel-btn");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", function () {
                panel.remove();
            });
        }
    }

    /* ── Event delegation for block content and markers ── */

    document.addEventListener("click", function (e) {
        if (presenting) return;  // read-only while presenting (#452)
        var contentEl = e.target.closest(".line-content");
        var markerEl = e.target.closest(".marker-btn");
        var lineNumEl = e.target.closest(".line-num");

        var startLine = null;
        var endLine = null;
        var el = contentEl || markerEl || lineNumEl;
        if (el) {
            startLine = parseInt(el.getAttribute("data-line-start"), 10);
            endLine = parseInt(el.getAttribute("data-line-end"), 10);
        }

        if (startLine && endLine) {
            if (isMobile) {
                showBlockCommentsMobile(startLine, endLine);
            } else {
                showBlockComments(startLine, endLine);
            }
        }
    });

    /* ── SPA file switching (#447) ──
     *
     * Nav-tree clicks fetch /api/source and re-render through the already-warm
     * Pyodide runtime instead of doing a full page load (which would re-boot
     * it).  The URL scheme is unchanged (?path=, via history.pushState), and
     * every failure path falls back to the plain full-page /view navigation,
     * so the server-side render stays the no-JS / degraded fallback.
     *
     * The DOM-free decision/derivation logic lives in static/nav_logic.js so
     * it can be unit-tested directly (test_spa_nav.js).
     */

    var navLogic = window.docReviewNavLogic;
    var sourceBody = document.querySelector("#source tbody");
    var navSeq = 0;  // guards against a slow response clobbering a newer one

    /* A renderer is only "warm" once it can do everything a swap needs: render
     * blocks AND build the row/TOC/header specs (the Python builders, #451).
     * Anything less falls back to a full-page /view. */
    function warmRenderer() {
        var r = window.docReviewRenderer;
        if (!r) return null;
        var needed = [
            "renderBlocks", "sourceRowSpecs", "tocItemSpecs", "headerFields",
            "presentationSpecs",
        ];
        for (var i = 0; i < needed.length; i++) {
            if (typeof r[needed[i]] !== "function") return null;
        }
        return r;
    }

    function setLineAttrs(el, spec) {
        el.setAttribute("data-line-start", spec.startLine);
        el.setAttribute("data-line-end", spec.endLine);
    }

    function buildSourceRows(renderer, blocks) {
        var specs = renderer.sourceRowSpecs(blocks, commentsData);
        var frag = document.createDocumentFragment();
        for (var i = 0; i < specs.length; i++) {
            var spec = specs[i];

            var tr = document.createElement("tr");
            tr.id = spec.id;
            tr.className = spec.rowClass;
            setLineAttrs(tr, spec);

            var num = document.createElement("td");
            num.className = "line-num";
            setLineAttrs(num, spec);
            num.textContent = spec.label;
            tr.appendChild(num);

            var content = document.createElement("td");
            content.className = "line-content";
            setLineAttrs(content, spec);
            content.innerHTML = spec.html;
            tr.appendChild(content);

            var marker = document.createElement("td");
            marker.className = "line-marker";
            if (spec.commentCount) {
                var btn = document.createElement("button");
                btn.className = "marker-btn";
                setLineAttrs(btn, spec);
                btn.title = spec.commentCount + " comment(s)";
                btn.textContent = String(spec.commentCount);
                marker.appendChild(btn);
            }
            tr.appendChild(marker);

            frag.appendChild(tr);
        }
        return frag;
    }

    function renderToc(renderer, toc) {
        if (!fileNav) return;
        var existing = fileNav.querySelector(".toc-section");
        if (existing) existing.remove();

        var specs = renderer.tocItemSpecs(toc);
        if (!specs.length) return;

        var section = document.createElement("div");
        section.className = "toc-section";
        var heading = document.createElement("div");
        heading.className = "toc-heading";
        heading.textContent = "Contents";
        section.appendChild(heading);

        var list = document.createElement("ul");
        list.className = "toc-list";
        for (var i = 0; i < specs.length; i++) {
            var li = document.createElement("li");
            li.className = specs[i].className;
            var a = document.createElement("a");
            a.href = specs[i].href;
            a.className = "toc-link";
            a.textContent = specs[i].text;
            li.appendChild(a);
            list.appendChild(li);
        }
        section.appendChild(list);
        fileNav.appendChild(section);
    }

    function updateNavActive() {
        var links = navTreeEl.querySelectorAll(".file-nav-link");
        for (var i = 0; i < links.length; i++) {
            var link = links[i];
            var active = link.getAttribute("data-path") === currentPath;
            link.classList.toggle("active", active);
            if (!active) continue;
            // Expand the ancestor directories of the newly active file.
            var parent = link.parentNode;
            while (parent && parent !== navTreeEl) {
                if (parent.tagName === "DETAILS") parent.open = true;
                parent = parent.parentNode;
            }
        }
    }

    function updateHeader(renderer, data) {
        var fields = renderer.headerFields(data);
        var title = document.querySelector(".file-header h1");
        if (title) title.textContent = fields.title;
        var fidEl = document.querySelector(".file-header .file-id");
        if (fidEl) fidEl.textContent = fields.fileIdLabel;
        document.title = fields.documentTitle;

        // Comments must be posted against the file now on screen, not the one
        // the page was originally served with.
        var tpl = formTpl.content;
        tpl.querySelector('[name="file_id"]').value = fields.formFileId;
        tpl.querySelector('[name="path"]').value = fields.formPath;
    }

    function setEmbeddedJson(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = JSON.stringify(value);
    }

    function applyDocument(renderer, data, blocks) {
        // A swap replaces the document under the deck; never present the
        // previous file's slides over the new one.
        if (presenting) exitPresentation();
        commentsData = data.comments_by_block || {};
        currentPath = data.path;
        currentBlocks = blocks;
        deckSpecs = null;  // the new file is a different deck (or none)
        updateHeader(renderer, data);
        sourceBody.innerHTML = "";
        sourceBody.appendChild(buildSourceRows(renderer, blocks));
        renderToc(renderer, data.toc);
        updateNavActive();
        document.querySelectorAll(".inline-comments").forEach(function (el) {
            el.remove();
        });
        sidebar.innerHTML =
            '<p class="sidebar-hint">Click a block to add a comment.</p>';
        // Keep the embedded blobs consistent with what is on screen.
        setEmbeddedJson("comments-data", commentsData);
        setEmbeddedJson("current-path-data", currentPath);
        setEmbeddedJson("source-data", data.source);
        refreshPresentToggle();
    }

    /* Restore the "#L42" line anchor after a swap (Back/Forward onto a deep
     * link), otherwise start at the top of the new document. */
    function restoreScroll() {
        var anchorId = navLogic.lineAnchorId(window.location.hash);
        var target = anchorId ? document.getElementById(anchorId) : null;
        if (target) {
            target.scrollIntoView();
        } else {
            window.scrollTo(0, 0);
        }
    }

    /* Returns true when a soft swap was started (the caller then suppresses
     * the default navigation), false when soft navigation is impossible. */
    function softNavigate(path, push) {
        var renderer = warmRenderer();
        if (!renderer || !sourceBody) return false;

        var seq = ++navSeq;
        fetch(navLogic.apiSourceUrl(path), {
            headers: { Accept: "application/json" },
        })
            .then(function (resp) {
                if (!resp.ok) throw new Error("HTTP " + resp.status);
                return resp.json();
            })
            .then(function (data) {
                if (seq !== navSeq) return;  // superseded by a later click
                applyDocument(renderer, data, renderer.renderBlocks(data.source));
                if (push) {
                    history.pushState(
                        { path: path }, "", navLogic.viewUrl(path)
                    );
                }
                restoreScroll();
                if (typeof renderer.initMermaid === "function") {
                    renderer.initMermaid();
                }
            })
            .catch(function (err) {
                if (seq !== navSeq) return;
                console.warn("Soft navigation failed, falling back:", err);
                if (push) {
                    window.location.href = navLogic.viewUrl(path);
                } else {
                    window.location.reload();
                }
            });
        return true;
    }

    if (navLogic) {
        document.addEventListener("click", function (e) {
            var link = e.target.closest && e.target.closest("a.file-nav-link");
            if (!link) return;
            var path = link.getAttribute("data-path");
            if (!navLogic.shouldIntercept(e, path, currentPath, warmRenderer())) {
                return;  // plain href → full-page /view navigation
            }
            if (softNavigate(path, true)) e.preventDefault();
        });

        window.addEventListener("popstate", function (e) {
            var path = (e.state && e.state.path) || getPath();
            var action = navLogic.popstateAction(
                path, currentPath, warmRenderer()
            );
            if (action === "ignore") return;  // hash-only change: keep the DOM
            if (action === "soft" && softNavigate(path, false)) return;
            window.location.reload();
        });
    }

    /* ── Marp presentation mode (#452) ──
     *
     * Strictly read-only: the comment UI is unmounted while presenting and
     * restored, with every comment still on its original block, on the way
     * back.  That holds because a slide is only a *grouping* of the same row
     * specs review mode renders (view_specs.slide_specs) — the blocks keep
     * their token.map line ranges and their id="L{start}" anchors in both
     * modes, so nothing re-anchors.
     *
     * The toggle is pure client-side: the review DOM is hidden, not destroyed,
     * and Pyodide is never re-booted.
     */

    var presentBtn = document.getElementById("present-toggle");
    var viewLayout = document.querySelector(".view-layout");
    var currentBlocks = null;   // blocks for the file on screen
    var deckSpecs = null;       // cached presentation_specs for that file
    var deckEl = null;
    var slideIndex = 0;

    function documentSource() {
        var el = document.getElementById("source-data");
        return el ? JSON.parse(el.textContent) : "";
    }

    /* Slide specs for the file on screen, built once per document.  The blocks
     * are reused from the last render when we have them, so a toggle costs no
     * markdown re-parse and — crucially — no Pyodide re-boot. */
    function presentationSpecs(renderer) {
        if (deckSpecs) return deckSpecs;
        var source = documentSource();
        if (!currentBlocks) currentBlocks = renderer.renderBlocks(source);
        deckSpecs = renderer.presentationSpecs(
            currentBlocks, commentsData, source
        );
        return deckSpecs;
    }

    function refreshPresentToggle() {
        if (!presentBtn) return;
        var renderer = warmRenderer();
        var specs = null;
        if (renderer) {
            try {
                specs = presentationSpecs(renderer);
            } catch (err) {
                console.warn("Presentation mode unavailable:", err);
            }
        }
        presentBtn.hidden = !(specs && specs.available);
    }

    function buildDeck(specs) {
        var deck = document.createElement("div");
        deck.className = "presentation theme-" + specs.theme;
        deck.tabIndex = -1;  // focusable, so the keys reach the deck

        for (var i = 0; i < specs.slides.length; i++) {
            var slide = specs.slides[i];
            var section = document.createElement("section");
            section.className = "slide";
            section.setAttribute("data-slide", slide.index);

            for (var j = 0; j < slide.rows.length; j++) {
                var row = slide.rows[j];
                var blockEl = document.createElement("div");
                blockEl.className = "slide-block";
                // The review-mode anchor, carried onto the slide: same block,
                // same line range, whichever mode you are looking at.
                setLineAttrs(blockEl, row);
                blockEl.innerHTML = row.html;
                section.appendChild(blockEl);
            }

            if (specs.paginate) {
                var num = document.createElement("div");
                num.className = "slide-number";
                num.textContent = slide.number + " / " + specs.slides.length;
                section.appendChild(num);
            }
            deck.appendChild(section);
        }
        return deck;
    }

    function showSlide(index) {
        if (!deckEl) return;
        var sections = deckEl.querySelectorAll(".slide");
        slideIndex = navLogic.clampSlide(index, sections.length);
        for (var i = 0; i < sections.length; i++) {
            sections[i].hidden = i !== slideIndex;
        }
        window.scrollTo(0, 0);
    }

    function enterPresentation() {
        var renderer = warmRenderer();
        if (!renderer || presenting) return;
        var specs = presentationSpecs(renderer);
        if (!specs || !specs.slides.length) return;

        // Unmount the comment UI — presenting is read-only, so it must not be
        // reachable, not merely invisible.
        document.querySelectorAll(".inline-comments").forEach(function (el) {
            el.remove();
        });
        sidebar.innerHTML =
            '<p class="sidebar-hint">Click a block to add a comment.</p>';
        document.querySelectorAll(".source-line.active").forEach(function (el) {
            el.classList.remove("active");
        });

        deckEl = buildDeck(specs);
        // The review DOM is hidden, never destroyed: coming back is a re-show,
        // with every comment still attached to its block.
        if (viewLayout) viewLayout.hidden = true;
        document.body.classList.add("presenting");
        document.body.appendChild(deckEl);
        presentBtn.classList.add("active");
        showSlide(0);
        deckEl.focus();
    }

    function exitPresentation() {
        if (!presenting && !deckEl) return;
        if (deckEl) {
            deckEl.remove();
            deckEl = null;
        }
        if (viewLayout) viewLayout.hidden = false;
        document.body.classList.remove("presenting");
        if (presentBtn) presentBtn.classList.remove("active");
        presenting = false;
    }

    if (presentBtn && navLogic) {
        presentBtn.addEventListener("click", function () {
            if (presenting) {
                exitPresentation();
            } else {
                enterPresentation();
                presenting = !!deckEl;
            }
        });

        document.addEventListener("keydown", function (e) {
            if (!presenting) return;
            var action = navLogic.presentationAction(e.key);
            if (!action) return;
            e.preventDefault();
            if (action === "exit") exitPresentation();
            else showSlide(slideIndex + (action === "next" ? 1 : -1));
        });

        // The Present button only makes sense once the Python builders are
        // reachable, which is when the Pyodide runtime finishes warming.
        document.addEventListener("docreview:renderer-ready", refreshPresentToggle);
    }
})();
