/* steer public snapshot: read-only. Everything on screen comes from data.js (a fixed copy made at build time); this page
   makes no network requests and has nothing to submit. Status words are the ones the studio computed from the stored
   checks and the review log; nothing here claims a clinician signed anything unless the review log records that step. */
(function () {
  "use strict";
  var D = window.STEER_PUBLIC, $ = function (s) { return document.querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var rc = function (c) { return String(c).toLowerCase().replace(/_/g, " "); };
  var IST = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  var ist = function (iso) { var d = new Date(iso); return isNaN(d) ? String(iso) : IST.format(d) + " IST"; };
  var NAV = [["index.html", "Run overview"], ["ledger.html", "Claim ledger"], ["posts.html", "Finished posts"], ["history.html", "Review history"]];
  var cls = function (c) { return c.bucket === "rejected" ? "st-rejected" : (c.status_word === "verified" ? "st-passed" : "st-needs"); };
  var chip = function (c) { return '<span class="chip ' + cls(c) + '">' + esc(c.status_word) + "</span>"; };
  var aid = function (k) { return "claim-" + String(k).replace("/", "-"); };   // ledger anchor of one claim: claim-<pmcid>-<claim id>
  var paperOf = function (k) { return D.papers[k]; };
  var count = function (xs, f) { var o = {}; xs.forEach(function (x) { f(x).forEach(function (k) { o[k] = (o[k] || 0) + 1; }); }); return o; };
  var list = function (o) { var k = Object.keys(o).sort(function (a, b) { return o[b] - o[a]; }); return k.length ? "<ul>" + k.map(function (x) { return "<li>" + esc(rc(x)) + " &times; " + o[x] + "</li>"; }).join("") + "</ul>" : '<span class="muted">none</span>'; };

  function shell(body) {
    var cur = document.body.getAttribute("data-page");
    $("#main").insertAdjacentHTML("beforebegin",
      '<div class="proto" role="note"><strong>Read-only demo copy: actions disabled.</strong> An independent prototype, not an official Steer product. Built ' + esc(ist(D.generated_at)) + ".</div>" +
      '<header class="top"><a class="wordmark" href="index.html">steer</a><nav aria-label="Pages"><ul>' + NAV.map(function (n) { return '<li><a href="' + n[0] + '"' + (n[0] === cur ? ' aria-current="page"' : "") + ">" + n[1] + "</a></li>"; }).join("") + "</ul></nav></header>");
    $("#main").innerHTML = body;
    document.body.insertAdjacentHTML("beforeend", '<footer><span><b>steer</b> public snapshot</span><span>' + esc(D.clinician_review) + ". Editorial review is not clinical approval.</span></footer>");
  }

  function overview() {
    var out = "<h1>What the pipeline did, run by run</h1><p class=\"lede\">Each run read one paper, proposed claims with their exact quotes, and checked every one. <strong>Verified</strong> means the code checks and an independent model cross-check both passed (or a named person cleared the flag and the cross-check still passed). <strong>Needs check</strong> means a person still has to look. <strong>Rejected</strong> means the gate refused it. " + esc(D.clinician_review) + ".</p>";
    Object.keys(D.papers).forEach(function (k) {
      var p = D.papers[k], cs = D.claims.filter(function (c) { return c.paper === k; }), c = p.citation || {};
      var n = function (w) { return cs.filter(function (x) { return x.status_word === w; }).length; };
      var posts = D.posts.filter(function (x) { return x.paper === k; });
      out += '<section class="card" style="margin-top:18px"><h2 style="margin-top:0">' + esc(p.short) + "</h2><p class=\"small\">" + esc((c.authors || []).slice(0, 3).join(", ")) + (c.authors && c.authors.length > 3 ? " et al." : "") + ". " + esc(c.title) + ". <em>" + esc(c.journal) + "</em>, " + esc(c.year) + ". " +
        (p.pmc_url ? '<a href="' + esc(p.pmc_url) + '">' + esc(p.pmcid) + "</a>" : "") + " &middot; licence: " + esc(p.license || "not stated") + (p.page_images ? " (page images shown)" : " (page images not shown)") + "</p>" +
        '<div class="stats"><div class="stat big"><b>' + cs.length + "</b><span>proposed</span></div><div class=\"stat\"><b>" + n("verified") + "</b><span>verified</span></div><div class=\"stat\"><b>" + n("needs check") + "</b><span>needs check</span></div><div class=\"stat\"><b>" + n("rejected") + "</b><span>rejected</span></div><div class=\"stat\"><b>" + posts.length + "</b><span>posts</span></div></div>" +
        '<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr))"><div><h3>Why claims were rejected</h3>' + list(count(cs.filter(function (x) { return x.bucket === "rejected"; }), function (x) { return x.reasons; })) +
        "</div><div><h3>Why claims need a check</h3>" + list(count(cs.filter(function (x) { return x.status_word === "needs check"; }), function (x) { return x.reasons; })) + "</div></div>" +
        '<p class="small"><a href="ledger.html#' + esc(k) + '">Open the claim ledger for this paper</a>' + (posts.length ? ' &middot; <a href="posts.html#' + esc(k) + '">see its posts</a>' : "") + "</p></section>";
    });
    shell(out);
  }

  function pageBox(c, p) {
    if (!p.page_images || p.no_page_layout || !c.pages) return c.page_note ? '<p class="small muted">' + esc(c.page_note) + "</p>" : "";
    return c.pages.map(function (pg) {
      var sz = (p.page_sizes_pt || {})[pg], bx = (c.boxes || {})[pg] || [];
      return '<div class="pagebox" style="margin:8px 0"><img loading="lazy" alt="Page ' + pg + ' of the paper, with the quoted lines highlighted" src="pages/' + esc(c.paper) + "-p" + pg + '.jpg">' +
        (sz ? bx.map(function (b) { var r = b.bbox; return '<div class="hl" style="left:' + (100 * r[0] / sz[0]) + "%;top:" + (100 * r[1] / sz[1]) + "%;width:" + (100 * (r[2] - r[0]) / sz[0]) + "%;height:" + (100 * (r[3] - r[1]) / sz[1]) + '%"></div>'; }).join("") : "") + "</div>";
    }).join("");
  }
  function hist(k) {
    var t = "<strong>" + esc(rc(k.code)) + "</strong> cleared by " + esc(k.reviewer) + " (" + esc(k.role) + ") &middot; " + esc(ist(k.at)) + " &mdash; " + esc(k.reason);
    return k.revoked ? '<span class="revoked">' + t + " &mdash; revoked by " + esc(k.revoked.reviewer) + " (" + esc(k.revoked.role) + ") &middot; " + esc(ist(k.revoked.at)) + " &mdash; " + esc(k.revoked.reason) + "</span>" : t;
  }
  function ledger() {
    var out = "<h1>Every claim, with its quote and its source</h1><p class=\"lede\">Claim, then the exact quote from the paper, the citation, where it is, what the checks said, and the post line that uses it. Rejected and unchecked claims are listed too.</p>";
    out += '<p class="small">' + Object.keys(D.papers).map(function (k) { return '<a href="#' + esc(k) + '">' + esc(D.papers[k].short) + "</a>"; }).join(" &middot; ") + "</p>";
    Object.keys(D.papers).forEach(function (k) {
      var p = D.papers[k], c0 = p.citation || {}, cs = D.claims.filter(function (c) { return c.paper === k; });
      out += '<h2 id="' + esc(k) + '">' + esc(p.short) + ' <span class="small muted">' + cs.length + " claims</span></h2>";
      cs.forEach(function (c) {
        var loc = c.pages ? "page " + c.pages.join(", ") : (p.no_page_layout ? "no page layout for this source; section text below" : "not stated");
        out += '<details class="claim" id="' + esc(aid(c.key)) + '"><summary>' + chip(c) + ' <span class="small muted">' + esc(c.claim_id) + '</span><span class="ctext">' + esc(c.claim_text) + "</span></summary><div class=\"body\">" +
          "<h3>Exact quote</h3><blockquote>" + esc(c.quote) + "</blockquote>" +
          '<dl class="kv"><dt>Status</dt><dd>' + chip(c) + " " + esc(c.status) + "</dd>" +
          "<dt>Source</dt><dd>" + esc((c0.authors || [])[0] || "") + " et al., " + esc(c0.journal) + " " + esc(c0.year) + (c0.doi ? ", doi " + esc(c0.doi) : "") + (p.pmc_url ? ' &middot; <a href="' + esc(p.pmc_url) + '">' + esc(p.pmcid) + "</a>" : "") + "</dd>" +
          "<dt>Location</dt><dd>" + esc(loc) + (c.sids && c.sids.length ? " &middot; " + esc(c.sids.join(", ")) : "") + "</dd>" +
          "<dt>Population</dt><dd>" + esc(c.population || "not stated") + "</dd><dt>Comparison</dt><dd>" + esc(c.comparison_group || "not stated") + "</dd>" +
          "<dt>Outcome</dt><dd>" + esc(c.outcome || "not stated") + "</dd><dt>Time point</dt><dd>" + esc(c.time_point || "not stated") + "</dd>" +
          "<dt>Gate</dt><dd>" + esc(c.gate_status || "") + (c.gate_reasons && c.gate_reasons.length ? ": " + esc(c.gate_reasons.map(function (r) { return rc(r.code) + (r.detail ? " (" + r.detail + ")" : ""); }).join("; ")) : "") + "</dd>" +
          "<dt>Code flags</dt><dd>" + (c.flags && c.flags.length ? esc(c.flags.map(function (f) { return rc(f.code) + ": " + (f.detail || ""); }).join("; ")) : "none") + "</dd>" +
          "<dt>Review reasons</dt><dd>" + (c.bucket !== "rejected" && c.reasons && c.reasons.length ? esc(c.reasons.map(rc).join(", ")) : "none") + "</dd>" +
          "<dt>Cross-check</dt><dd>" + (c.crosscheck ? esc(c.crosscheck.verdict) + " (" + esc(rc(c.crosscheck.independence || "")) + ")" + (c.crosscheck.reason ? " &mdash; " + esc(c.crosscheck.reason) : "") : "not run") + "</dd>" +
          "<dt>Used in</dt><dd>" + (c.used_in.length ? c.used_in.map(function (u) { return '<a href="posts.html#' + esc(u.post) + '">' + esc(u.post) + "</a>: &ldquo;" + esc(u.line_text) + "&rdquo;"; }).join("<br>") : '<span class="muted">no post uses it</span>') + "</dd></dl>" +
          (c.clearance_history.length ? '<h3>Review history</h3><div class="review-status"><div class="clearances">' + c.clearance_history.map(function (h) { return '<p class="small">' + hist(h) + "</p>"; }).join("") + "</div></div>" : "") +
          (c.context ? "<h3>Section text</h3><div class=\"ctx\">" + c.context.map(function (s) { return '<p class="' + (s.hit ? "hit" : "") + '">' + esc(s.text) + "</p>"; }).join("") + "</div>" : "") +
          pageBox(c, p) + "</div></details>";
      });
    });
    shell(out);
  }

  function posts() {
    var out = "<h1>Finished posts</h1><p class=\"lede\">Drafts as the pipeline wrote and rendered them, each with its true review status. Where a reel exists it is linked. A line that states a health fact links to the claim behind it.</p>";
    if (!D.posts.length) out += '<p class="note">No posts in this snapshot.</p>';
    D.posts.forEach(function (p) {
      var st = p.stages;
      out += '<section class="card post" id="' + esc(p.id) + '" style="margin-top:18px"><h2 style="margin-top:0">' + esc(p.id) + ' <span class="small muted">' + esc(p.format || "") + " &middot; " + esc(D.papers[p.paper] ? D.papers[p.paper].short : "") + "</span></h2>" +
        '<p><span class="chip st-needs">' + esc(p.status) + "</span></p>" +
        '<ol class="stages">' + ["code", "layout", "editorial", "clinical"].map(function (k) { return "<li><b>" + ({ code: "Code checks", layout: "Layout check", editorial: "Editorial review", clinical: "Clinical approval" })[k] + "</b>: " + esc(st[k].words) + "</li>"; }).join("") + "</ol>" +
        (p.message ? "<p><strong>Message.</strong> " + esc(p.message) + "</p>" : "") +
        (p.pngs.length ? '<div class="slides" style="margin:12px 0">' + p.pngs.map(function (u, i) { return '<a href="' + esc(u) + '"><img alt="Slide ' + (i + 1) + " of " + esc(p.id) + '" loading="lazy" style="width:100%;height:auto;border:1px solid var(--clay-text)" src="' + esc(u) + '"></a>'; }).join("") + "</div>" : '<p class="muted">No rendered images for this post.</p>') +
        (p.reel ? '<p><a href="' + esc(p.reel) + '">Watch the reel (MP4)</a></p>' : '<p class="small muted">No reel for this post yet.</p>') +
        '<div class="tablewrap"><table class="lines stack2"><caption>Lines and the claims behind them</caption><thead><tr><th>Kind</th><th>Line</th><th>Claims</th><th>Checks</th></tr></thead><tbody>' +
        p.lines.map(function (l) { return '<tr><td data-l="Kind">' + esc(l.kind) + '</td><td data-l="Line" class="wide">' + esc(l.text) + '</td><td data-l="Claims">' + (l.claim_ids.length ? l.claim_ids.map(function (i) { return '<a href="ledger.html#' + esc(aid(p.paper + "/" + i)) + '">' + esc(i) + "</a>"; }).join(", ") : "&mdash;") + '</td><td data-l="Checks">' + (l.failures.length ? esc(l.failures.map(rc).join(", ")) : "passed") + "</td></tr>"; }).join("") + "</tbody></table></div></section>";
    });
    shell(out);
  }

  function history() {
    var out = "<h1>Review history</h1><p class=\"lede\">Every recorded reviewer action, in the order it happened. The log is append-only: a clearance that was later revoked stays here, followed by the revocation and any new clearance. Reasons are shown as written. Reviewers identified themselves by name (prototype, no login).</p>";
    if (!D.history.length) out += '<p class="note">No review actions have been recorded.</p>';
    else out += '<div class="tablewrap card"><table class="hist"><caption>' + D.history.length + ' actions</caption><thead><tr><th>#</th><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Reason</th></tr></thead><tbody>' +
      D.history.map(function (a) { return '<tr><td data-l="#">' + a.id + '</td><td data-l="When">' + esc(ist(a.at)) + '</td><td data-l="Who">' + esc(a.reviewer) + " (" + esc(a.role) + ')</td><td data-l="Action">' + esc(rc(a.action)) + (a.clears ? " (" + esc(rc(a.clears)) + ")" : "") + (a.revokes ? " of #" + a.revokes : "") + '</td><td data-l="On">' + (a.target_kind === "claim" ? '<a href="ledger.html#' + esc(aid(a.target)) + '">' + esc(a.target) + "</a>" : '<a href="posts.html#' + esc(a.target) + '">' + esc(a.target) + "</a>") + '</td><td data-l="Reason" class="reason">' + esc(a.reason || "") + "</td></tr>"; }).join("") + "</tbody></table></div>";
    shell(out);
  }

  ({ "index.html": overview, "ledger.html": ledger, "posts.html": posts, "history.html": history })[document.body.getAttribute("data-page")]();
  if (location.hash) { var el = document.getElementById(decodeURIComponent(location.hash.slice(1))); if (el) { if (el.tagName === "DETAILS") el.open = true; el.scrollIntoView(); } }
})();
