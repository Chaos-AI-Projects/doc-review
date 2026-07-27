/* doc-review — minimal JS for comment interaction (block-anchored) */

(function () {
    "use strict";

    var commentsData = JSON.parse(
        document.getElementById("comments-data").textContent
    );

    var sidebar = document.getElementById("sidebar-content");
    var formTpl = document.getElementById("comment-form-tpl");
    var isMobile = window.matchMedia("(max-width: 768px)").matches;

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
})();
