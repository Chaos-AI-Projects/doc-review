/* doc-review — minimal JS for comment interaction */

(function () {
    "use strict";

    var commentsData = JSON.parse(
        document.getElementById("comments-data").textContent
    );
    var repliesData = JSON.parse(
        document.getElementById("replies-data").textContent
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

    /* ── Build comment HTML ── */

    function escapeHtml(s) {
        var d = document.createElement("div");
        d.textContent = s;
        return d.innerHTML;
    }

    function commentCardHtml(c, depth) {
        depth = depth || 0;
        var cls = "comment-card" + (c.resolved ? " resolved" : "");
        var html = '<div class="' + cls + '">';
        html +=
            '<div class="comment-meta"><strong>' +
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
        if (depth === 0) {
            html +=
                '<button class="action-link reply-btn" data-comment-id="' +
                c.id +
                '" data-line="' +
                c.line_start +
                '">Reply</button>';
        }
        html += "</div>";

        // replies
        var replies = repliesData[String(c.id)] || [];
        if (replies.length > 0) {
            html += '<div class="reply-thread">';
            for (var i = 0; i < replies.length; i++) {
                html += commentCardHtml(replies[i], depth + 1);
            }
            html += "</div>";
        }

        html += "</div>";
        return html;
    }

    function getPath() {
        return new URLSearchParams(window.location.search).get("path") || "";
    }

    /* ── Show comments for a line in the sidebar ── */

    function showLineComments(lineNum) {
        var comments = commentsData[String(lineNum)] || [];
        var html = "<h3>Line " + lineNum + "</h3>";
        for (var i = 0; i < comments.length; i++) {
            html += commentCardHtml(comments[i], 0);
        }

        // New comment form
        var frag = formTpl.content.cloneNode(true);
        var form = frag.querySelector("form");
        form.querySelector('[name="line_start"]').value = lineNum;
        form.querySelector('[name="line_end"]').value = lineNum;
        var tmp = document.createElement("div");
        tmp.appendChild(frag);
        html += tmp.innerHTML;

        sidebar.innerHTML = html;

        // Wire cancel
        var cancelBtn = sidebar.querySelector(".cancel-btn");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", function () {
                sidebar.innerHTML =
                    '<p class="sidebar-hint">Click a line to add a comment.</p>';
            });
        }

        // Wire reply buttons
        var replyBtns = sidebar.querySelectorAll(".reply-btn");
        for (var j = 0; j < replyBtns.length; j++) {
            replyBtns[j].addEventListener("click", handleReply);
        }

        // Highlight active line
        document.querySelectorAll(".source-line.active").forEach(function (el) {
            el.classList.remove("active");
        });
        var row = document.getElementById("L" + lineNum);
        if (row) row.classList.add("active");
    }

    /* ── Mobile: toggle inline comment panel under the line ── */

    function showLineCommentsMobile(lineNum) {
        // Remove existing inline panels
        document.querySelectorAll(".inline-comments").forEach(function (el) {
            el.remove();
        });

        var row = document.getElementById("L" + lineNum);
        if (!row) return;

        var comments = commentsData[String(lineNum)] || [];
        var html = "";
        for (var i = 0; i < comments.length; i++) {
            html += commentCardHtml(comments[i], 0);
        }
        var frag = formTpl.content.cloneNode(true);
        var form = frag.querySelector("form");
        form.querySelector('[name="line_start"]').value = lineNum;
        form.querySelector('[name="line_end"]').value = lineNum;
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

        var replyBtns = panel.querySelectorAll(".reply-btn");
        for (var j = 0; j < replyBtns.length; j++) {
            replyBtns[j].addEventListener("click", handleReply);
        }
    }

    /* ── Reply handler ── */

    function handleReply(e) {
        var btn = e.currentTarget;
        var commentId = btn.getAttribute("data-comment-id");
        var lineNum = btn.getAttribute("data-line");
        var card = btn.closest(".comment-card");
        if (!card) return;

        // Don't add duplicate forms
        if (card.querySelector(".comment-form")) return;

        var frag = formTpl.content.cloneNode(true);
        var form = frag.querySelector("form");
        form.querySelector('[name="line_start"]').value = lineNum;
        form.querySelector('[name="line_end"]').value = lineNum;
        form.querySelector('[name="parent_id"]').value = commentId;
        card.appendChild(frag);

        var cancelBtn = card.querySelector(".cancel-btn");
        if (cancelBtn) {
            cancelBtn.addEventListener("click", function () {
                var f = card.querySelector(".comment-form");
                if (f) f.remove();
            });
        }
    }

    /* ── Event delegation for line numbers, line content, and markers ── */

    document.addEventListener("click", function (e) {
        var lineEl = e.target.closest(".line-num");
        var contentEl = e.target.closest(".line-content");
        var markerEl = e.target.closest(".marker-btn");

        var lineNum = null;
        if (lineEl) lineNum = parseInt(lineEl.getAttribute("data-line"), 10);
        else if (contentEl)
            lineNum = parseInt(contentEl.getAttribute("data-line"), 10);
        else if (markerEl)
            lineNum = parseInt(markerEl.getAttribute("data-line"), 10);

        if (lineNum) {
            if (isMobile) {
                showLineCommentsMobile(lineNum);
            } else {
                showLineComments(lineNum);
            }
        }
    });
})();
