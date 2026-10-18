/* steer studio. Local (`steer studio --serve`): reads /api/data and posts reviewer actions to the server, which
   decides everything (roles, stage order, exports). Read-only copy (`--export`): reads window.STEER_DATA from data.js,
   shows the banner, and has no action buttons. Every status word comes from the server as checked. */
(function () {
  "use strict";
  var F, LIVE;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var esc = function (s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]; }); };
  var rc = function (code) { return String(code).toLowerCase().replace(/_/g, " "); };
  var ND = '<span class="muted">not stated in the data</span>';
  var val = function (v) { return v == null || v === "" ? ND : esc(v); };
  var KEYS, byKey = {};
  var GLOSS = {
    LAYOUT_CHECK_NEEDED: "The quote matches the extracted text, but the page reading order is uncertain. A person needs to look at the highlighted lines on the page and confirm they say what the claim says.",
    CONTEXT_NOT_IN_QUOTE: "The claim names a population, comparison, outcome or time point that the quote itself does not state.",
    NUMBER_NOT_IN_QUOTE: "A number in the claim was not found in the quote, so the gate rejected the claim."
  };
  var REASONS = ["inaccurate", "overstated", "missing context", "tone/brand", "other"];

  /* ---------- reviewer + server calls (local studio only) ---------- */
  function reviewer() { try { return localStorage.getItem("steer-reviewer") || ""; } catch (e) { return ""; } }
  function setReviewer(v) { try { localStorage.setItem("steer-reviewer", v); } catch (e) { /* per-viewer convenience only */ } }
  function post(path, body) {
    return fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }
  function act(body, out) {
    body.reviewer = reviewer();
    if (!body.reviewer) { out.textContent = "Type your name at the top first (it must be listed in reviewers.toml)."; return; }
    post("/api/action", body).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, code: r.status, j: j }; }); })
      .then(function (r) {
        if (r.ok) { out.textContent = "Recorded: " + r.j.after_status; setTimeout(function () { location.reload(); }, 700); }
        else out.textContent = r.code === 400 ? r.j.error : "Refused (" + r.code + (r.j.stage ? ", stage " + r.j.stage : "") + "): " + r.j.error;
      });
  }

  /* ---------- shell ---------- */
  var NAV = [["index.html", "Run overview"], ["ledger.html", "Evidence ledger"], ["claim.html", "Claim check"], ["posts.html", "Post review"], ["export.html", "Export"]];
  function shell() {
    var cur = document.body.getAttribute("data-page");
    var nav = NAV.map(function (n) { return '<li><a href="' + n[0] + '"' + (n[0] === cur ? ' aria-current="page"' : "") + ">" + n[1] + "</a></li>"; }).join("");
    var who = LIVE ? '<label class="small">Reviewer <input id="who" value="' + esc(reviewer()) + '" placeholder="your name" style="width:12em"></label>' +
      ' <span class="small muted">' + esc(F.reviewers.note) + "</span>"
      : '<span class="small muted">read-only copy</span>';
    document.body.insertAdjacentHTML("afterbegin",
      '<a class="skip" href="#main">Skip to content</a>' +
      (F.public ? '<div class="proto" role="note"><strong>' + esc(F.banner || "demo copy: actions disabled") + ".</strong> Hackathon prototype, not an official steer product. Generated " + esc(F.generated_at) + ".</div>"
        : '<div class="proto" role="note"><strong>Hackathon prototype.</strong> Not an official steer product. Local studio: actions are recorded in the run database.</div>') +
      '<header class="top"><a class="wordmark" href="index.html" aria-label="steer studio, home">steer</a>' +
      '<nav aria-label="Studio screens"><ul>' + nav + "</ul></nav>" + who + "</header>");
    document.body.insertAdjacentHTML("beforeend",
      '<footer><span><b>steer</b> &middot; studio prototype built at a hackathon, not reviewed or endorsed by steer clinicians</span><span>steerclub.com</span></footer>');
    var w = $("#who"); if (w) w.addEventListener("change", function () { setReviewer(w.value.trim()); });
  }

  /* ---------- claim helpers ---------- */
  function stClass(c) { return c.bucket === "rejected" ? "st-rejected" : (c.usable ? "st-passed" : "st-needs"); }
  var IST = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
  function ist(iso) { var d = new Date(iso); return isNaN(d) ? String(iso) : IST.format(d) + " IST"; }
  // any ISO time inside a stored status text, shown in IST like the rest of the page
  function istText(t) { return String(t).replace(/\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d(?:\.\d+)?)?(?:Z|[+-]\d\d:\d\d)/g, ist); }
  function statusPill(c) { return '<span class="chip ' + stClass(c) + '">' + esc(c.status_word) + "</span>"; }
  function reviewStatus(c) {
    var cl = c.clearances || [];
    return '<div class="card review-status" style="margin-top:14px"><h3>Review status</h3><p class="small">Current status: ' + statusPill(c) + "</p>" +
      (cl.length ? '<div class="clearances">' + cl.map(function (k) { return "<p class=\"small\"><strong>" + esc(k.code) + "</strong> cleared by " + esc(k.reviewer) + " (" + esc(k.role) + ") &middot; " + esc(ist(k.at)) + " &mdash; " + esc(k.reason) + "</p>"; }).join("") + "</div>"
        : '<p class="small muted">No flag has been cleared yet.</p>') + "</div>";
  }
  function pageText(c) { return c.pages ? "page " + c.pages.join(", ") : "NO_PAGE_LAYOUT (no page exists for this source; locate by sentence)"; }
  function postsUsing(c) {
    if (!c.used_in.length) return '<span class="muted">none</span>';
    return c.used_in.map(function (u) { return '<a href="posts.html#' + u.post + '">' + esc(u.post) + "</a>: " + esc(u.line_text); }).join("<br>");
  }
  function list(xs, f) { return xs.length ? "<ul>" + xs.map(f).join("") + "</ul>" : "none"; }
  function claimFields(c) {
    var p = F.papers[c.paper], cit = p.citation;
    var cc = c.crosscheck ? esc(c.crosscheck.verdict) + " (" + esc(c.crosscheck.independence || "no independence recorded") + (c.crosscheck.model ? ", " + esc(c.crosscheck.model) : "") + ")" : "not run";
    return '<dl class="kv">' +
      "<dt>Claim ID</dt><dd>" + esc(c.claim_id) + " (" + esc(p.short) + ", " + esc(c.paper) + ")</dd>" +
      "<dt>Status</dt><dd>" + statusPill(c) + '<div class="small">' + esc(istText(c.status)) + "</div></dd>" +
      "<dt>Claim text</dt><dd>" + esc(c.claim_text) + "</dd>" +
      "<dt>Source</dt><dd>" + esc(cit.title) + ", " + esc(cit.journal) + " " + esc(cit.year) + ' (<a href="https://doi.org/' + esc(cit.doi) + '">doi ' + esc(cit.doi) + "</a>, licence " + esc(cit.license) + ")</dd>" +
      "<dt>Exact quote</dt><dd><blockquote>" + esc(c.quote) + "</blockquote></dd>" +
      "<dt>Sentence ids</dt><dd>" + esc(c.sids.join(", ")) + "</dd><dt>Line ids</dt><dd>" + esc(c.line_ids.join(", ")) + "</dd>" +
      "<dt>Page</dt><dd>" + esc(pageText(c)) + "</dd>" +
      "<dt>Population</dt><dd>" + val(c.population) + "</dd><dt>Comparison group</dt><dd>" + val(c.comparison_group) + "</dd>" +
      "<dt>Outcome</dt><dd>" + val(c.outcome) + "</dd><dt>Time point</dt><dd>" + val(c.time_point) + "</dd>" +
      "<dt>Limitations</dt><dd>" + val(c.limitations) + "</dd>" +
      "<dt>Numbers as parsed</dt><dd>" + (c.numbers_checked.length ? esc(c.numbers_checked.join("; ")) : "none checked") + "</dd>" +
      "<dt>Hedges</dt><dd>claim marked hedged: " + (c.hedged ? "yes" : "no") + "; direction: " + val(c.direction) + "; type: " + val(c.claim_type) + "</dd>" +
      "<dt>Gate result</dt><dd><strong>" + esc(c.gate_status) + "</strong>" + list(c.gate_reasons, function (r) { return "<li>" + esc(rc(r.code)) + ": " + esc(r.detail) + "</li>"; }) + "</dd>" +
      "<dt>Gate warnings</dt><dd>" + list(c.gate_warnings, function (w) { return "<li>" + esc(rc(w.code)) + ": " + esc(w.detail) + "</li>"; }) + "</dd>" +
      "<dt>Code flags</dt><dd>" + list(c.flags, function (w) { return "<li>" + esc(rc(w.code)) + ": " + esc(w.detail) + clearedNote(c, w.code) + "</li>"; }) + "</dd>" +
      "<dt>Review reasons</dt><dd>" + list(c.bucket === "rejected" ? [] : c.reasons, function (r) { return "<li>" + esc(rc(r)) + clearedNote(c, r) + "</li>"; }) + "</dd>" +
      "<dt>Library cross-check</dt><dd>" + cc + "</dd>" +
      "<dt>Labels</dt><dd>" + (c.labels && c.labels.theme ? esc(rc(c.labels.theme)) + (c.labels.doubts && c.labels.doubts.length ? "; doubts: " + esc(c.labels.doubts.map(rc).join(", ")) : "") : '<span class="muted">none</span>') + "</dd>" +
      (c.excluded ? "<dt>Excluded from posts</dt><dd>" + esc(c.excluded) + "</dd>" : "") +
      "<dt>Used in post lines</dt><dd>" + postsUsing(c) + "</dd></dl>";
  }
  // a cleared flag stays listed with its clearance: an editorial disposition, never clinical approval
  function clearedNote(c, code) {
    var k = (c.clearances || []).filter(function (x) { return x.code === code; })[0];
    return k ? '<div class="small cleared-note">' + esc(code) + " cleared by " + esc(k.reviewer) + " (" + esc(k.role) + ") &middot; " + esc(ist(k.at)) + " &mdash; " + esc(k.reason) + "</div>" : "";
  }
  function counts(xs, fn) { var o = {}; xs.forEach(function (c) { fn(c).forEach(function (k) { o[k] = (o[k] || 0) + 1; }); }); return o; }
  function rowsOf(o) { return Object.keys(o).sort(function (a, b) { return o[b] - o[a]; }).map(function (k) { return "<tr><td>" + esc(rc(k)) + '</td><td class="n">' + o[k] + "</td></tr>"; }).join(""); }
  var secs = function (v) { return typeof v === "number" ? v.toFixed(1) + " s" : "&ndash;"; };

  /* ---------- 1. overview ---------- */
  function overview() {
    var cmp = KEYS.map(function (k) {
      var p = F.papers[k], s = p.summary, ps = F.posts.filter(function (x) { return x.paper === k; });
      return '<tr><th scope="row"><a href="#' + k + '">' + esc(p.short) + '</a></th><td class="n" data-l="Claims proposed">' + s.proposed + '</td><td class="n" data-l="Passed">' + s.passed + '</td><td class="n" data-l="Needs check">' + s.needs_check + '</td><td class="n" data-l="Rejected by the gate">' + s.rejected + '</td><td class="n" data-l="Verified">' + (s.verified == null ? "&ndash;" : s.verified) + '</td><td class="n" data-l="Posts">' + ps.length + "</td></tr>";
    }).join("");
    $("#main").innerHTML =
      "<h1>What the pipeline <em>checked</em>, rejected and <em>could not check</em></h1>" +
      '<p class="lede">Every number below is read from the stored runs and posts; nothing is estimated. Statuses are computed when this page loads, from the checks and the reviewer actions recorded so far.</p>' +
      '<div class="note maroon"><strong>Clinical approval ' + (F.reviewers.has_clinician ? "needs a configured Steer clinician." : "is impossible here: no clinician is configured.") + "</strong> Posts stay pending doctor review until a Steer clinician approves them.</div>" +
      '<div class="tablewrap card"><table class="stack"><caption>The papers side by side</caption><thead><tr><th>Paper</th><th class="n">Claims proposed</th><th class="n">Passed</th><th class="n">Needs check</th><th class="n">Rejected by the gate</th><th class="n">Verified</th><th class="n">Posts</th></tr></thead><tbody>' + cmp + "</tbody></table>" +
      '<p class="small muted">Verified means the code checks and an independent supported cross-check both passed (one run each; counts change between runs because extraction is a fresh model call).</p></div>' +
      '<div class="grid g2" style="margin-top:18px">' + KEYS.map(paperCard).join("") + "</div>";
  }
  function paperCard(k) {
    var p = F.papers[k], s = p.summary, cs = F.claims.filter(function (c) { return c.paper === k; }), tot = s.proposed || 1;
    var needsBy = counts(cs.filter(function (c) { return c.bucket === "needs_check"; }), function (c) { return c.reasons; });
    var rej = counts(cs.filter(function (c) { return c.bucket === "rejected"; }), function (c) { return c.reasons.filter(function (x, i, a) { return a.indexOf(x) === i; }); });
    var t = p.timing_seconds, ps = F.posts.filter(function (x) { return x.paper === k; });
    var steps = Object.keys(t).map(function (n) { return "<tr><td>" + esc(rc(n)) + '</td><td class="n">' + secs(t[n]) + "</td></tr>"; }).join("");
    return '<section class="card" id="' + k + '"><h2 style="margin-top:0">' + esc(p.short) + "</h2>" +
      '<p class="small muted">' + esc(p.citation.title) + " &middot; " + esc(p.citation.journal) + " &middot; " + esc(k) + " &middot; licence " + esc(p.license) + "</p>" +
      "<h3>Ingest facts</h3><dl class=\"kv\"><dt>Pages</dt><dd>" + (p.no_page_layout ? "none: a PMC plain-text rendering with no page layout, so no page images and no boxes" : p.pages + " (" + esc(Object.keys(p.layouts).map(function (l) { return p.layouts[l] + " " + rc(l); }).join(", ")) + ")") + "</dd>" +
      "<dt>Ingest warnings</dt><dd>" + esc(Object.keys(p.ingest_warnings).map(function (w) { return rc(w) + " × " + p.ingest_warnings[w]; }).join(", ")) + "</dd><dt>Access</dt><dd>" + esc(p.access) + ", fetched via " + esc(p.fetched_via) + "</dd></dl>" +
      '<h3 style="margin-top:14px">Steps and times (recorded)</h3><div class="tablewrap"><table><thead><tr><th>Step</th><th class="n">Time</th></tr></thead><tbody>' + steps + "</tbody></table></div>" +
      '<h3 style="margin-top:14px">Claims</h3><div class="stats"><div class="stat"><b>' + s.proposed + '</b><span>proposed</span></div><div class="stat big"><b>' + s.passed + '</b><span>passed code checks</span></div><div class="stat"><b>' + s.needs_check + '</b><span>needs check</span></div><div class="stat"><b>' + s.rejected + "</b><span>rejected by the gate</span></div></div>" +
      '<div class="bar" role="img" aria-label="' + s.passed + " passed, " + s.needs_check + " needs check, " + s.rejected + ' rejected"><i class="b1" style="width:' + s.passed / tot * 100 + '%"></i><i class="b2" style="width:' + s.needs_check / tot * 100 + '%"></i><i class="b3" style="width:' + s.rejected / tot * 100 + '%"></i></div>' +
      '<div class="tablewrap"><table style="margin-top:12px"><caption>Needs check, by reason</caption><thead><tr><th>Reason</th><th class="n">Claims</th></tr></thead><tbody>' + rowsOf(needsBy) + "</tbody></table></div>" +
      '<div class="tablewrap"><table><caption>Rejected by the gate, by reason</caption><thead><tr><th>Reason</th><th class="n">Claims</th></tr></thead><tbody>' + rowsOf(rej) + "</tbody></table></div>" +
      '<p><a href="ledger.html#' + k + '">All ' + cs.length + " stored claims of this paper in the evidence ledger</a></p>" +
      "<h3>Sentence coverage</h3><p>" + (p.all_sentences_covered ? "All sentences covered" : "Not all sentences covered") + ": " + p.sentences_in_chunks + " of " + p.sentences_in_document + " sentences in " + p.chunks + " chunks.</p>" +
      "<h3>Model calls and tokens, as reported</h3><p>" + p.model_calls + " calls (" + esc(Object.keys(p.call_outcomes).map(function (o) { return p.call_outcomes[o] + " " + o; }).join(", ")) + ") on " + esc(p.models.join(", ")) + ". Tokens: " + p.tokens_in + " in, " + p.tokens_out + " out (as the connectors reported them).</p>" +
      "<h3>Posts</h3>" + (ps.length ? "<ul>" + ps.map(function (x) { return '<li><a href="posts.html#' + x.id + '">' + esc(x.id) + "</a> (" + esc(x.format) + "): " + esc(x.status) + "</li>"; }).join("") + "</ul>" : "<p>none written</p>") +
      "<h3>Estimated review time (timestamp-based)</h3><p><b>" + p.estimated_review_minutes + ' min</b> <span class="small muted">' + esc(F.review_time_method) + ".</span></p></section>";
  }

  /* ---------- 2. ledger ---------- */
  function ledger() {
    var reasons = {}; F.claims.forEach(function (c) { c.reasons.forEach(function (r) { reasons[r] = 1; }); });
    var h = location.hash.slice(1);
    $("#main").innerHTML =
      "<h1>Every claim, <em>including</em> the rejected ones</h1>" +
      '<p class="lede">The rejected log shows what the gate stopped. Filter by paper, status or reason. Open a row for every field; open the claim check to see the quote on the page.</p>' +
      '<form class="filters" role="search" aria-label="Filter claims" onsubmit="return false">' +
      '<label>Paper<select id="f-paper"><option value="">All papers</option>' + KEYS.map(function (k) { return '<option value="' + k + '"' + (h === k ? " selected" : "") + ">" + esc(F.papers[k].short) + "</option>"; }).join("") + "</select></label>" +
      '<label>Status<select id="f-status"><option value="">All statuses</option><option value="passed">passed the gate</option><option value="needs_check">needs check</option><option value="rejected">rejected by the gate</option><option value="usable">usable in posts (verified)</option></select></label>' +
      '<label>Reason<select id="f-reason"><option value="">Any reason</option>' + Object.keys(reasons).sort().map(function (r) { return '<option value="' + r + '">' + esc(rc(r)) + "</option>"; }).join("") + "</select></label>" +
      '<label>Search claim or quote<input id="f-q" type="search" placeholder="for example semaglutide"></label></form>' +
      '<p id="count" class="small" aria-live="polite"></p><div id="list"></div>';
    var limit = 40;
    function render(reset) {
      if (reset !== false) limit = 40;
      var fp = $("#f-paper").value, fs = $("#f-status").value, fr = $("#f-reason").value, q = $("#f-q").value.toLowerCase();
      var rows = F.claims.filter(function (c) {
        return (!fp || c.paper === fp) && (!fs || (fs === "usable" ? c.usable : c.bucket === fs)) && (!fr || c.reasons.indexOf(fr) >= 0) && (!q || (c.claim_text + " " + c.quote).toLowerCase().indexOf(q) >= 0);
      });
      $("#count").textContent = "Matching " + rows.length + " of " + F.claims.length + " claims; listing the first " + Math.min(limit, rows.length) + ".";
      $("#list").innerHTML = rows.slice(0, limit).map(function (c) {
        return '<details class="claim" data-k="' + esc(c.key) + '"><summary>' + statusPill(c) + ' <span class="small muted">' + esc(F.papers[c.paper].short) + " &middot; " + esc(c.claim_id) + " &middot; " + esc(c.sids[0] || "") + " &middot; " + esc(c.pages ? "page " + c.pages.join(", ") : "no page layout") + '</span><span class="ctext">' + esc(c.claim_text) + '</span></summary><div class="body"></div></details>';
      }).join("") + (rows.length > limit ? '<p><button type="button" id="more">Show 40 more</button></p>' : "");
      var m = $("#more"); if (m) m.addEventListener("click", function () { limit += 40; render(false); });
    }
    $("#list").addEventListener("toggle", function (e) {
      var d = e.target; if (!d.open || d.getAttribute("data-filled")) return;
      var c = byKey[d.getAttribute("data-k")];
      $(".body", d).innerHTML = claimFields(c) + '<p><a href="claim.html#' + encodeURIComponent(c.key) + '">Open the claim check for this claim</a></p>';
      d.setAttribute("data-filled", "1");
    }, true);
    ["f-paper", "f-status", "f-reason"].forEach(function (id) { $("#" + id).addEventListener("change", render); });
    $("#f-q").addEventListener("input", render);
    render();
  }

  /* ---------- 3. claim check ---------- */
  function claimPage() {
    var key = decodeURIComponent(location.hash.slice(1));
    if (!byKey[key]) { var firstLayout = F.claims.filter(function (x) { return x.needs_layout; })[0] || F.claims[0]; key = firstLayout.key; }
    var c = byKey[key], p = F.papers[c.paper];
    var opts = KEYS.map(function (k) {
      return '<optgroup label="' + esc(F.papers[k].short) + '">' + F.claims.filter(function (x) { return x.paper === k; }).map(function (x) {
        return '<option value="' + esc(x.key) + '"' + (x.key === key ? " selected" : "") + ">" + esc(x.claim_id + " - " + x.status.slice(0, 40) + " - " + x.claim_text.slice(0, 50)) + "</option>";
      }).join("") + "</optgroup>";
    }).join("");
    var left;
    if (c.pages && p.page_images) {
      left = c.pages.map(function (pg) {
        var sz = p.page_sizes_pt[pg], bx = c.boxes[pg] || [], src = (LIVE ? "/pages/" : "pages/") + c.paper + "-p" + pg + ".jpg";
        return '<figure style="margin:0 0 14px"><div class="pagebox"><img src="' + src + '" width="' + Math.round(sz[0] / 72 * 130) + '" height="' + Math.round(sz[1] / 72 * 130) + '" alt="Page ' + pg + " of " + esc(p.short) + ", the quote's " + bx.length + ' lines outlined in rust">' +
          bx.map(function (b) { var x = b.bbox; return '<span class="hl" title="' + b.line_id + '" style="left:' + x[0] / sz[0] * 100 + "%;top:" + x[1] / sz[1] * 100 + "%;width:" + (x[2] - x[0]) / sz[0] * 100 + "%;height:" + (x[3] - x[1]) / sz[1] * 100 + '%"></span>'; }).join("") +
          '</div><figcaption class="small muted">Page ' + pg + ". Highlight drawn from the stored line boxes of " + bx.map(function (b) { return b.line_id; }).join(", ") + ".</figcaption></figure>";
      }).join("");
    } else if (c.pages) {
      left = '<div class="card ctx"><h3>' + esc(c.page_note || "page image not shown") + '</h3><p class="small muted">This copy is public, and the paper&rsquo;s licence (' + esc(p.license) + ') does not allow republishing its pages. The quote&rsquo;s line ids: ' + esc(c.line_ids.join(", ")) + ' (page ' + esc(c.pages.join(", ")) + '). Read the page at <a href="' + esc(p.pmc_url) + '">' + esc(c.paper) + " on PubMed Central</a>.</p>" +
        c.sentences.map(function (s) { return '<p class="hit"><span class="small muted">' + s.sid + "</span><br>" + esc(s.text) + "</p>"; }).join("") + "</div>";
    } else {
      left = '<div class="card ctx"><h3>No page image for this source</h3><p class="small muted">This source has no page layout (PMC&rsquo;s plain-text rendering). No page number is shown and none is invented. The quote&rsquo;s sentence among its neighbours; source: <a href="' + esc(p.pmc_url) + '">' + esc(c.paper) + " on PubMed Central</a>.</p>" +
        (c.context || []).map(function (s) { return '<p class="' + (s.hit ? "hit" : "") + '"><span class="small muted">' + s.sid + (s.hit ? ", the quoted sentence" : "") + "</span><br>" + esc(s.text) + "</p>"; }).join("") + "</div>";
    }
    var warnWords = c.gate_warnings.concat(c.flags, c.gate_reasons).filter(function (w, i, a) { return a.findIndex(function (z) { return z.code === w.code; }) === i; }).map(function (w) { return GLOSS[w.code] ? "<li><strong>" + esc(rc(w.code)) + ".</strong> " + esc(GLOSS[w.code]) + "</li>" : ""; }).join("");
    var noLayout = !c.pages, can = LIVE && c.needs_layout;
    var decision = LIVE ? (can ? '<label class="small muted" for="why">Reason (required, at least 40 characters and 6 words: say what you checked and where)</label><br><textarea id="why" rows="2" style="width:100%"></textarea>' +
        '<div class="decision"><button type="button" id="ok">' + (noLayout ? "Matches the source text" : "Matches the page") + '</button><button type="button" class="alt" id="no">Does not match</button></div><p id="out" class="small" aria-live="polite"></p>'
        : '<p class="small muted">No layout decision is needed for this claim (the gate raised no layout warning).</p>')
      : '<p class="small muted">Read-only copy: no actions.</p>';
    $("#main").innerHTML =
      "<h1>Does the quote <em>match</em> the page?</h1>" +
      '<form class="filters" onsubmit="return false"><label style="width:min(100%,640px)">Choose a claim<select id="pick" style="width:100%;min-width:0">' + opts + "</select></label></form>" +
      "<p>" + statusPill(c) + ' <span class="small muted">' + esc(p.short) + " &middot; " + esc(c.claim_id) + " &middot; " + esc(pageText(c).split(" (")[0]) + "</span></p>" +
      '<div class="split"><div>' + left + "</div><div>" +
      '<div class="card"><h3>The claim</h3><p>' + esc(c.claim_text) + "</p><h3>The exact quote</h3><blockquote>" + esc(c.quote) + "</blockquote>" +
      "<h3>Extracted sentence" + (c.sentences.length > 1 ? "s" : "") + "</h3>" + c.sentences.map(function (s) { return '<p class="small"><span class="muted">' + s.sid + "</span><br>" + esc(s.text) + "</p>"; }).join("") + "</div>" +
      '<div class="card" style="margin-top:14px"><h3>What the gate and the code flags said</h3>' + (warnWords ? "<ul>" + warnWords + "</ul>" : "<p>No warnings that need a person.</p>") + "</div>" +
      '<div class="card" style="margin-top:14px"><h3>Layout decision</h3>' + decision + "</div>" + reviewStatus(c) + clearForm(c) + "</div></div>" +
      '<h2>All recorded fields</h2><div class="card">' + claimFields(c) + "</div>";
    document.querySelectorAll(".pagebox img").forEach(function (im) {
      var miss = function () { im.parentNode.innerHTML = '<p class="card" style="margin:0"><strong>Page image not available.</strong> The quote&rsquo;s line ids are ' + esc(c.line_ids.join(", ")) + ".</p>"; };
      if (im.complete && !im.naturalWidth) miss(); else im.addEventListener("error", miss);
    });
    $("#pick").addEventListener("change", function (e) { location.hash = "#" + encodeURIComponent(e.target.value); location.reload(); });
    var clr = $("#clr-ok");
    if (clr) clr.addEventListener("click", function () { act({ action: "check_cleared", target: c.target, clears: $("#clr-code").value, reason: $("#clr-why").value }, $("#clr-out")); });
    if (can) {
      $("#ok").addEventListener("click", function () { act({ action: "layout_checked", target: c.target, reason: $("#why").value }, $("#out")); });
      $("#no").addEventListener("click", function () { act({ action: "layout_unresolved", target: c.target, reason: $("#why").value }, $("#out")); });
    }
  }

  function clearForm(c) {
    if (!LIVE || !c.clearable || !c.clearable.length) return "";
    return '<div class="card" style="margin-top:14px"><h3>Review an automated flag</h3><p class="small">Clear a flag only if it is a false alarm, and say why. The flag stays on record next to your clearance. This is an editorial disposition, not clinical approval.</p>' +
      '<label class="small muted">Flag <select id="clr-code">' + c.clearable.map(function (r) { return '<option value="' + esc(r) + '">' + esc(rc(r)) + "</option>"; }).join("") + "</select></label>" +
      '<br><label class="small muted" for="clr-why">Why it is a false alarm (required, at least 40 characters and 6 words)</label><br><textarea id="clr-why" rows="3" style="width:100%"></textarea>' +
      '<div class="decision"><button type="button" id="clr-ok">Clear this flag</button></div><p id="clr-out" class="small" aria-live="polite"></p></div>';
  }

  /* ---------- 4. post review ---------- */
  var STAGE_NAMES = { code: "Code checks", layout: "Layout check", editorial: "Editorial review", clinical: "Clinical approval" };
  function stageChip(s) { return '<span class="chip ' + (s.done ? "st-passed" : "st-needs") + '">' + esc(s.words) + "</span>"; }
  var human = rc;   // "different_provider" -> "different provider"; the raw code stays in the badge's title
  function badge(text, cls, title) { return '<span class="chip ' + (cls || "") + '"' + (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(text) + "</span>"; }
  function lineRow(po, ln, i) {
    var claims = ln.claim_ids.map(function (id) { var c = byKey[po.paper + "/" + id]; return c ? '<a href="claim.html#' + encodeURIComponent(c.key) + '">' + esc(id) + "</a>" : esc(id); }).join(" ") || '<span class="muted">none</span>';
    var x = ln.crosscheck;
    var cc = x ? badge(human(x.verdict) + (x.independence ? " · " + human(x.independence) : ""), x.verdict === "supported" ? "st-passed" : "st-rejected", x.verdict + (x.independence ? " / " + x.independence : "") + (x.model ? " / " + x.model : ""))
      : badge("not cross-checked", "", ln.kind + " lines are not cross-checked");
    var fails = ln.failures.length ? ln.failures.map(function (f) { return badge(human(f.code), "st-rejected", f.code + (f.detail ? ": " + f.detail : "")); }).join("") : badge("passed", "st-passed");
    return '<tr><td data-l="#">' + (i + 1) + '</td><td data-l="Where" class="small muted">' + esc(ln.path) + '</td><td data-l="Kind">' + badge(ln.kind) + '</td><td data-l="Text" class="wide">' + esc(ln.text) + '</td><td data-l="Claims">' + claims + '</td><td data-l="Code checks">' + fails + '</td><td data-l="Cross-check">' + cc + "</td></tr>";
  }
  function posts() {
    var only = location.hash.slice(1);
    var shown = F.posts.filter(function (x) { return !only || x.id === only; });
    var html = "<h1>The post review <em>gate</em></h1>" +
      '<p class="lede">Each post moves through four stages in order: code checks, layout check, editorial review, clinical approval. A stage can be acted on only when the ones before it are complete; the server enforces it.</p>' +
      '<div class="note maroon"><strong>' + (F.reviewers.has_clinician ? "Clinical approval needs a Steer clinician." : "No clinician is configured: clinical approval is impossible here.") + "</strong> Every post stays pending doctor review until a Steer clinician approves it.</div>" +
      (only ? '<p><a href="posts.html">Show all ' + F.posts.length + " posts</a></p>" : "");
    shown.forEach(function (po) {
      var p = F.papers[po.paper], st = po.stages;
      var gallery = po.pngs.length ? '<div class="slides">' + po.pngs.map(function (src) { return '<img src="' + esc(src) + '" alt="' + esc(po.id) + " artwork " + esc(src.split("/").pop()) + '" loading="lazy" style="width:220px;height:auto;border-radius:6px">'; }).join("") + "</div>" : '<p class="muted">No artwork: a blocked post gets no PNGs.</p>';
      var edOpen = LIVE && po.first_open === "editorial", clOpen = LIVE && po.first_open === "clinical" && F.reviewers.has_clinician;
      var acts = LIVE ? '<label class="small muted">Reason (required to reject)<br><textarea class="why" rows="2" style="width:100%"></textarea></label>' +
        '<label class="small muted">Reason category <select class="cat">' + REASONS.map(function (r) { return "<option>" + r + "</option>"; }).join("") + "</select></label>" +
        '<div class="decision"><button type="button" class="ed-ok"' + (edOpen ? "" : " disabled") + '>Editorial approve</button><button type="button" class="alt ed-no"' + (edOpen ? "" : " disabled") + '>Editorial reject</button>' +
        '<button type="button" class="cl-ok"' + (clOpen ? "" : " disabled") + ">" + (F.reviewers.has_clinician ? "Clinical approve" : "Clinical approve (needs a Steer clinician)") + '</button><button type="button" class="alt cl-no"' + (clOpen ? "" : " disabled") + ">Clinical reject</button></div>" +
        '<p class="small out" aria-live="polite"></p>' : '<p class="small muted">Read-only copy: no actions.</p>';
      html += '<section class="card post" id="' + po.id + '" data-id="' + po.id + '" style="margin-top:18px"><h2 style="margin-top:0">' + esc(po.id) + ' <span class="small muted">' + esc(po.format) + " &middot; " + esc(p.short) + (po.has_reel ? " &middot; reel" : "") + "</span></h2>" +
        "<p>Post status: " + '<span class="chip ' + (po.first_open ? "st-needs" : "st-passed") + '">' + esc(po.status) + "</span></p>" + gallery +
        "<h3>Message</h3><p>" + esc(po.message) + "</p><h3>Reasoning</h3><p>" + esc(po.reasoning) + "</p>" +
        '<h3>Four stages</h3><ol class="stages">' + ["code", "layout", "editorial", "clinical"].map(function (k) { return "<li><b>" + STAGE_NAMES[k] + "</b>: " + stageChip(st[k]) + "</li>"; }).join("") + "</ol>" +
        '<div class="tablewrap"><table class="stack2 lines" style="margin-top:14px"><caption>Every line, its claims and its checks</caption><thead><tr><th>#</th><th>Where</th><th>Kind</th><th>Text</th><th>Claims</th><th>Code checks</th><th>Cross-check</th></tr></thead><tbody>' + po.lines.map(function (ln, i) { return lineRow(po, ln, i); }).join("") + "</tbody></table></div>" + acts + "</section>";
    });
    $("#main").innerHTML = html;
    if (!LIVE) return;
    document.querySelectorAll("section.post").forEach(function (sec) {
      var id = sec.getAttribute("data-id"), out = $(".out", sec);
      var reason = function () { return { reason: $(".why", sec).value, reason_category: $(".cat", sec).value }; };
      var on = function (cls, body) { var b = $(cls, sec); if (b) b.addEventListener("click", function () { act(body(), out); }); };
      on(".ed-ok", function () { return { action: "editorial_approved", target: id }; });
      on(".ed-no", function () { return Object.assign({ action: "editorial_rejected", target: id }, reason()); });
      on(".cl-ok", function () { return { action: "clinically_approved", target: id }; });
      on(".cl-no", function () { return Object.assign({ action: "clinically_rejected", target: id }, reason()); });
    });
  }

  /* ---------- 5. export ---------- */
  function download(path, body, out, name) {
    post(path, body).then(function (r) {
      if (r.ok) return r.blob().then(function (b) { var a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = name; a.click(); out.textContent = "Downloaded " + name + "."; });
      return r.json().then(function (j) { out.textContent = "Refused (" + r.status + (j.stage ? ", stage " + j.stage : "") + "): " + j.error; });
    });
  }
  function exportPage() {
    var rows = F.posts.map(function (po) {
      var st = po.stages;
      return '<tr><td data-l="Post"><label>' + (LIVE ? '<input type="checkbox" class="pick" value="' + po.id + '"> ' : "") + '<a href="posts.html#' + po.id + '">' + esc(po.id) + '</a></label></td><td data-l="Code checks">' + esc(st.code.words) + '</td><td data-l="Layout check">' + esc(st.layout.words) + '</td><td data-l="Editorial review">' + esc(st.editorial.words) + '</td><td data-l="Clinical approval">' + esc(st.clinical.words) + "</td>" +
        (LIVE ? '<td><button type="button" class="pub" data-id="' + po.id + '">Publishable export</button></td>' : "") + "</tr>";
    }).join("");
    $("#main").innerHTML =
      "<h1>Two ways to <em>export</em></h1>" +
      '<p class="lede">A draft pack can carry any post, and every image in it keeps &ldquo;draft &middot; pending doctor review&rdquo;; its manifest records all four stages. A publishable export needs all four stages complete and makes a clean re-render; otherwise the server refuses and names the missing stage.</p>' +
      '<div class="tablewrap card"><table class="stack2"><caption>Where every post stands</caption><thead><tr><th>Post</th><th>Code checks</th><th>Layout check</th><th>Editorial review</th><th>Clinical approval</th>' + (LIVE ? "<th>Publishable</th>" : "") + "</tr></thead><tbody>" + rows + "</tbody></table></div>" +
      (LIVE ? '<div class="decision"><button type="button" id="draft">Export a draft pack of the selected posts</button></div><p id="out" class="small" aria-live="polite"></p>'
        : '<p class="small muted">Read-only copy: exports run in the local studio only.</p>');
    if (!LIVE) return;
    $("#draft").addEventListener("click", function () {
      var ids = Array.prototype.map.call(document.querySelectorAll(".pick:checked"), function (x) { return x.value; });
      if (!ids.length) { $("#out").textContent = "Select at least one post."; return; }
      download("/api/export/draft-pack", { post_ids: ids }, $("#out"), "steer-draft-pack.zip");
    });
    document.querySelectorAll(".pub").forEach(function (b) {
      b.addEventListener("click", function () { download("/api/export/publishable", { post_id: b.getAttribute("data-id") }, $("#out"), "steer-publishable-" + b.getAttribute("data-id") + ".zip"); });
    });
  }

  function start(data) {
    F = data; LIVE = !F.public;
    KEYS = Object.keys(F.papers);
    F.claims.forEach(function (c) { byKey[c.key] = c; });
    shell();
    var pg = document.body.getAttribute("data-page");
    ({ "index.html": overview, "ledger.html": ledger, "claim.html": claimPage, "posts.html": posts, "export.html": exportPage })[pg]();
    document.querySelectorAll(".tablewrap").forEach(function (w) { w.tabIndex = 0; w.setAttribute("role", "region"); w.setAttribute("aria-label", "Table, scrolls sideways on small screens"); });
  }
  if (window.STEER_DATA) start(window.STEER_DATA);
  else fetch("/api/data").then(function (r) { return r.json(); }).then(start).catch(function () {
    document.getElementById("main").innerHTML = "<p>The studio data could not be loaded. Run <code>steer studio --serve</code>, or open the read-only copy made by <code>steer studio --export</code>.</p>";
  });
})();
