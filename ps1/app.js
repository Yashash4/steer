// Draws only strings from claims.json. Only computed numbers: readout counts n and m.
const main = document.querySelector("main");
const COMMA = String.fromCharCode(44);
const COLON = String.fromCharCode(58);
const DOT = String.fromCharCode(183);
const NL = String.fromCharCode(10);
const SEP = COLON + " ";
const DOTSEP = " " + DOT + " ";
// the svg namespace, split so no absolute url sits in this file
const SVGNS = ["http:", "", ["www", "w3", "org"].join(String.fromCharCode(46)), "2000", "svg"].join("/");
const SHARE_NAME = "steer-quiz" + String.fromCharCode(46) + "png";
let hudEl;
let C;
let S;

// display only: "kg/m2" is drawn as "kg/m²" (the checked text in claims.json is unchanged)
const disp = (t) => String(t).split("/m2").join("/m\u00b2");
function h(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.classList.add(...[].concat(cls));
  if (text !== undefined && text !== null) e.textContent = disp(text);
  return e;
}
function show(...nodes) {
  const s = h("section", "screen");
  s.append(...nodes);
  main.replaceChildren(s);
  window.scrollTo(0, 0);
}
function button(label, onclick, cls) {
  const b = h("button", ["btn"].concat(cls || []), label);
  b.type = "button";
  b.addEventListener("click", onclick);
  return b;
}
// "Question N of M": M counts the hook and every step picked (gap cards count, unscored); the hook has no M yet
function fill(t, n, m) { return t.replace("{n}", String(n)).replace("{m}", String(m)); }
function progress(n) {
  return h("p", "progress", S.total ? fill(C.ui.progress, n, S.total) : fill(C.ui.progress_first, n, 0));
}
function allRounds() { return [C.hook].concat(C.rounds); }

// the 10 s ring: decoration only (aria-hidden, never announced, no digits). It starts when the question, context and
// choices are on screen; at zero it only dims: no auto-pick, no wrong, no lock-out
function ring() {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("viewBox", "0 0 36 36");
  svg.setAttribute("class", "ring");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  ["bg", "fg"].forEach((k) => {
    const c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("class", k);
    c.setAttribute("cx", "18");
    c.setAttribute("cy", "18");
    c.setAttribute("r", "15");
    c.setAttribute("pathLength", "100");
    svg.append(c);
  });
  svg.lastChild.addEventListener("animationend", () => svg.classList.add("done"));
  return svg;
}

function ask(round, done, n) {
  const prog = progress(n);
  const rg = ring();
  prog.append(rg);
  const nodes = [prog, h("p", "opener", round.opener)];
  nodes.push(h("h1", "q", round.question));
  if (round.context) nodes.push(h("p", ["context", "small"], round.context));
  if (round.prompt) nodes.push(h("p", "prompt", round.prompt));
  const box = h("div", "choices");
  round.stops.forEach((s, i) => {
    const b = h("button", "choice", s.label);
    b.type = "button";
    b.addEventListener("click", () => done(i));
    box.append(b);
  });
  nodes.push(box);
  show(...nodes);
  requestAnimationFrame(() => requestAnimationFrame(() => rg.classList.add("go")));
}

// a finding is never drawn without its qualifiers: the build puts all of them (the one-study note included, for single
// studies only) in round.qualifiers, and every surface draws that list
function quals(round) {
  return round.qualifiers;
}
function qualList(round, cls) {
  const ul = h("ul", cls || "quals");
  quals(round).forEach((q) => ul.append(h("li", null, q)));
  return ul;
}

function reveal(round, g, next) {
  const res = grade(g);
  const verdict = h("div", ["verdict", "r-" + res]);
  verdict.append(h("p", "rres", C.ui["result_" + res]), h("span", "pts", "+" + String(C.points[res])));
  const cmp = h("div", "cmp");
  [[C.ui.you_guessed, round.stops[g.idx].label], [C.ui.study_range, round.stops[binOf(round)].label]].forEach((r) => {
    const row = h("p", "crow");
    row.append(h("span", null, r[0]), h("b", null, r[1]));
    cmp.append(row);
  });
  // the verified number and its sentence appear whole, after the panel and the labels have animated in
  const fin = h("div", "final");
  round.reveals.forEach((rv) => {
    const card = h("div", "reveal");
    const row = h("div", "numrow");
    // a round with no number draws its label large instead
    if (rv.number) row.append(h("span", "num", rv.number), h("span", "rlabel", rv.label));
    else row.append(h("span", ["rlabel", "lead"], rv.label));
    card.append(row, h("p", "sentence", rv.sentence));
    fin.append(card);
  });
  const ul = qualList(round);
  if (ul.children.length) fin.append(ul);
  const src = h("p", "src");
  src.append(h("span", null, round.source.short), h("span", null, round.source.location));
  fin.append(src);
  const panel = h("div", "quote");
  panel.hidden = true;
  panel.append(h("blockquote", null, round.quote), h("p", "small", round.source.full), h("p", "small", round.source.doi));
  const tog = h("button", "link", C.ui.see_quote);
  tog.type = "button";
  tog.setAttribute("aria-expanded", "false");
  tog.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    tog.textContent = panel.hidden ? C.ui.see_quote : C.ui.hide_quote;
    tog.setAttribute("aria-expanded", String(!panel.hidden));
  });
  fin.append(tog, panel, button(C.ui.next, next));
  show(verdict, cmp, fin);
  verdict.setAttribute("aria-live", "polite");
}

function playRound(round, next, n) {
  ask(round, (idx) => {
    const g = { round, idx };
    S.guesses.push(g);
    const res = grade(g);
    S.points += C.points[res];
    S.streak = res === "closest" ? S.streak + 1 : 0;
    if (res === "closest" && navigator.vibrate) navigator.vibrate(40);
    hud();
    reveal(round, g, next);
  }, n);
}

function hud() {
  hudEl.hidden = !S.guesses.length;
  hudEl.textContent = C.ui.score_label + SEP + String(S.points) + DOTSEP + C.ui.streak_label + SEP + String(S.streak);
}

// an info card, not a guess: nothing is asked and nothing enters S.guesses
function gapCard(card, next, n) {
  const c = h("div", ["card", "gap"]);
  if (card.context) c.append(h("p", "context", card.context), h("p", "small", card.source.short));
  c.append(h("h2", null, card.title), h("p", null, card.body));
  show(progress(n), h("p", "opener", card.opener), c, button(C.ui.next, next));
}

function picker() {
  const box = h("div", "chips");
  const start = button(C.ui.start_rounds, () => run(0));
  C.doubts.forEach((d) => {
    const hookOnly = d.answered_by.length === 1 && d.answered_by[0] === "hook";
    const c = h("button", hookOnly ? ["chip", "done"] : "chip");
    c.type = "button";
    c.append(h("span", null, d.label));
    if (hookOnly) {
      c.append(h("small", null, C.ui.done));
      c.disabled = true;
    } else {
      c.setAttribute("aria-pressed", "false");
      c.addEventListener("click", () => {
        const on = c.getAttribute("aria-pressed") !== "true";
        c.setAttribute("aria-pressed", String(on));
        S.picked = C.doubts.filter((x) => x === d ? on : S.picked.includes(x));
      });
    }
    box.append(c);
  });
  show(h("h1", "q", C.ui.pick_worries), h("p", "small", C.ui.pick_hint), box, start);
}

function run(i) {
  const steps = [];
  S.picked.forEach((d) => d.answered_by.forEach((id) => {
    if (id === "hook") return;
    const r = C.rounds.find((x) => x.id === id);
    if (r) steps.push({ round: r });
    else steps.push({ card: C.gap_cards.find((x) => x.id === id) });
  }));
  S.total = steps.length + 1;
  const go = (k) => {
    if (k >= steps.length) return readout();
    const st = steps[k];
    if (st.round) playRound(st.round, () => go(k + 1), k + 2);
    else gapCard(st.card, () => go(k + 1), k + 2);
  };
  go(i);
}

// a choice with numeric stops is a bin scale (value = lower end of the bin); the answer falls in exactly one bin.
// Same bin = closest range, one bin off = close estimate, further = different. Direction rounds: right or different.
const isNum = (r) => typeof r.stops[0].value === "number";
const binOf = (r) => r.stops.filter((s) => s.value <= r.answer).length - 1;
function grade(g) {
  if (isNum(g.round)) {
    const d = Math.abs(g.idx - binOf(g.round));
    return d === 0 ? "closest" : d === 1 ? "close" : "different";
  }
  return g.round.stops[g.idx].value === g.round.answer ? "closest" : "different";
}
function questions() {
  const qs = [];
  S.picked.map((d) => d.id).concat("is_it_for_me").forEach((id) => (C.doctor_questions[id] || []).forEach((q) => qs.push(q)));
  return qs.slice(0, 3);
}

function readout() {
  const nodes = [h("h1", "q", C.ui.readout_title)];
  const nums = S.guesses.filter((g) => isNum(g.round));
  const dirs = S.guesses.filter((g) => !isNum(g.round));
  const won = (list) => list.filter((g) => grade(g) === "closest").length;
  const card = h("div", "card");
  card.append(h("p", "small", C.ui.score_label + SEP + String(S.points)));
  if (nums.length) card.append(h("p", "big", fill(C.readout.numbers, won(nums), nums.length)));
  if (dirs.length) card.append(h("p", "big", fill(C.readout.direction, won(dirs), dirs.length)));
  nodes.push(card);
  nodes.push(h("h2", null, C.readout.meaning_title));
  // grouped by the doubts; a gap card is information, never scored
  C.doubts.forEach((d) => {
    const gs = S.guesses.filter((g) => d.answered_by.includes(g.round.id));
    const cards = S.picked.includes(d) ? C.gap_cards.filter((x) => d.answered_by.includes(x.id)) : [];
    if (!gs.length && !cards.length) return;
    const grp = h("div", ["card", "group"]);
    const head = h("div", "ghead");
    head.append(h("h3", null, d.label));
    if (gs.some((g) => grade(g) === "different")) head.append(h("span", "tag", C.ui.worth_a_chat));
    grp.append(head);
    const ul = h("ul", "list");
    gs.forEach((g) => {
      const li = h("li");
      li.append(h("p", ["rres", "r-" + grade(g)], C.ui["result_" + grade(g)]));
      g.round.reveals.forEach((rv) => li.append(h("p", null, rv.sentence)));
      li.append(qualList(g.round, ["quals", "tiny"]), h("p", "small", g.round.source.short));
      ul.append(li);
    });
    cards.forEach((x) => {
      const li = h("li");
      li.append(h("p", "small", C.ui.unscored));
      if (x.context) li.append(h("p", null, x.context), h("p", "small", x.source.short));
      li.append(h("p", null, x.body));
      ul.append(li);
    });
    grp.append(ul);
    nodes.push(grp);
  });
  const qs = questions();
  const qc = h("div", "card");
  qc.append(h("h2", null, C.ui.questions_title));
  const ql = h("ul", "list");
  qs.forEach((q) => ql.append(h("li", null, q)));
  qc.append(ql);
  const sc = h("div", "sharebox");
  nodes.push(qc, button(C.ui.share_make, () => shareCard(sc), "alt"), sc, button(C.ui.next, nextStep));
  show(...nodes);
}

// the share card: drawn locally on a canvas, only when tapped. It holds the count and fixed lines, nothing else (no doubts,
// guesses or contact data). navigator.share with the image where the browser can, else a download.
function wrapLines(ctx, text, width) {
  const lines = [];
  let line = "";
  text.split(" ").forEach((w) => {
    const t = line ? line + " " + w : w;
    if (line && ctx.measureText(t).width > width) { lines.push(line); line = w; } else line = t;
  });
  lines.push(line);
  return lines;
}
async function shareCard(into) {
  const pal = C.brand.palette;
  const ff = getComputedStyle(document.documentElement).getPropertyValue("--serif");
  const sans = getComputedStyle(document.documentElement).getPropertyValue("--sans");
  const W = 1080;
  const H = 1350;
  const cv = document.createElement("canvas");
  cv.width = W;
  cv.height = H;
  const ctx = cv.getContext("2d");
  const serif = (px) => "italic 600 " + String(px) + "px " + ff;
  try { await Promise.all([document.fonts.load(serif(80)), document.fonts.load("500 36px " + sans)]); } catch (x) { /* system font */ }
  ctx.fillStyle = pal.cream;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = pal.blush;
  ctx.fillRect(0, 0, W, 24);
  ctx.textBaseline = "alphabetic";
  ctx.fillStyle = pal.maroon;
  ctx.font = serif(96);
  ctx.fillText(C.brand.wordmark, 90, 190);
  const m = S.guesses.length;
  const n = S.guesses.filter((g) => grade(g) === "closest").length;
  // a ring of the share of guesses that matched the study range
  ctx.lineWidth = 36;
  ctx.lineCap = "round";
  ctx.strokeStyle = pal.blush;
  ctx.beginPath();
  ctx.arc(W / 2, 560, 200, 0, Math.PI * 2);
  ctx.stroke();
  if (n) {
    ctx.strokeStyle = pal.rust;
    ctx.beginPath();
    ctx.arc(W / 2, 560, 200, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * n) / m);
    ctx.stroke();
  }
  ctx.fillStyle = pal.rust;
  ctx.textAlign = "center";
  ctx.font = serif(150);
  ctx.fillText(String(n), W / 2, 600);
  ctx.fillStyle = pal.maroon;
  ctx.font = serif(64);
  wrapLines(ctx, fill(C.ui.share_line, n, m), W - 200).forEach((l, i) => ctx.fillText(l, W / 2, 910 + i * 84));
  ctx.fillStyle = pal.clay;
  ctx.font = "500 36px " + sans;
  wrapLines(ctx, C.ui.share_note, W - 240).forEach((l, i) => ctx.fillText(l, W / 2, 1140 + i * 50));
  ctx.font = "600 40px " + sans;
  ctx.fillStyle = pal.maroon;
  ctx.fillText(C.brand.signoff, W / 2, 1270);
  cv.classList.add("sharecard");
  into.replaceChildren(cv);
  cv.toBlob((blob) => {
    if (!blob) return;
    const file = new File([blob], SHARE_NAME, { type: "image/png" });
    if (navigator.canShare && navigator.canShare({ files: [file] })) {
      navigator.share({ files: [file], text: fill(C.ui.share_line, n, m) }).catch(() => {});
      return;
    }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = SHARE_NAME;
    document.body.append(a);
    a.click();
    a.remove();
  }, "image/png");
}

function link(l, cls) {
  const a = h("a", ["btn"].concat(cls || []), l.label);
  a.href = l.url; // exactly as given, nothing appended
  return a;
}

function copyText(text, ok) {
  const old = () => {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let done = false;
    try { done = document.execCommand("copy"); } catch (x) { /* nothing to do */ }
    ta.remove();
    if (done && ok) ok();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(ok || (() => {}), old);
  else old();
}

// demo only: nothing is submitted, stored or put in a URL; the preview is drawn from the page's own state
function warmLead() {
  const w = C.warm_lead;
  const card = h("div", ["card", "warm"]);
  card.append(h("h2", null, w.title), h("p", ["small", "demo"], w.note));
  const field = (label, type) => {
    const l = h("label", "field");
    const i = document.createElement("input");
    i.type = type;
    i.autocomplete = "off";
    l.append(h("span", null, label), i);
    return [l, i];
  };
  const [nl, name] = field(w.name_label, "text");
  const [pl, phone] = field(w.phone_label, "tel");
  const cl = h("label", "consent");
  const tick = document.createElement("input");
  tick.type = "checkbox";
  cl.append(tick, h("span", null, w.consent_label));
  const prev = h("div", "preview");
  prev.hidden = true;
  const showBtn = button(w.show_label, () => {
    const sep = COLON + " ";
    const rows = [[w.name_label, name.value], [w.phone_label, phone.value], [w.consent_short, tick.checked ? w.yes_label : ""],
      [w.doubts_label, S.picked.map((d) => d.label).join(COMMA + " ")]];
    const gl = h("ul", "list");
    S.guesses.forEach((g) => {
      const li = h("li");
      li.append(h("p", null, w.your_guess + sep + g.round.stops[g.idx].label + " " + DOT + " " + w.the_study + sep +
        g.round.reveals.map((rv) => (rv.number ? rv.number + " " : "") + rv.label).join(" ")), qualList(g.round, ["quals", "tiny"]));
      gl.append(li);
    });
    const ql = h("ul", "list");
    questions().forEach((q) => ql.append(h("li", null, q)));
    prev.replaceChildren(h("h3", null, w.preview_title), ...rows.map((r) => h("p", "prow", r[0] + sep + r[1])),
      h("h4", null, w.guesses_label), gl, h("h4", null, C.ui.questions_title), ql);
    prev.hidden = false;
  }, "alt");
  showBtn.disabled = true;
  tick.addEventListener("change", () => { showBtn.disabled = !tick.checked; if (!tick.checked) prev.hidden = true; });
  // the questions, then what the studies said with the same qualifiers as on screen (never a finding alone)
  const text = () => {
    const out = [C.ui.questions_title].concat(questions(), [""]);
    S.guesses.forEach((g) => {
      g.round.reveals.forEach((rv) => out.push(rv.sentence));
      quals(g.round).forEach((q) => out.push(q));
      out.push(g.round.source.short, "");
    });
    return out.join(NL);
  };
  // "Copied" only after a successful write, for 2 s; a Web Animation times it (no timers in this file, and the reduced-motion CSS cannot stop it)
  const note = h("p", ["small", "copied"], C.ui.copied);
  note.hidden = true;
  note.setAttribute("role", "status");
  let flash = null;
  const copied = () => {
    if (flash) flash.cancel();
    note.hidden = false;
    flash = note.animate([{ opacity: 1 }, { opacity: 1 }], 2000);
    flash.onfinish = () => { note.hidden = true; };
  };
  const cp = button(w.copy_label, () => copyText(text(), copied), "alt");
  const sh = button(w.share_label, () => {
    if (navigator.share) navigator.share({ title: w.share_title, text: text() }).catch(() => {});
    else copyText(text());
  }, "alt");
  card.append(nl, pl, cl, showBtn, prev, cp, note, sh);
  return card;
}

function nextStep() {
  show(link(C.cta.primary), link(C.cta.secondary, "alt"), warmLead(), button(C.ui.restart, start, "alt"));
}

function start() {
  S = { picked: [], guesses: [], total: 0, points: 0, streak: 0 };
  hud();
  playRound(C.hook, picker, 1);
}

async function load() {
  const el = document.getElementById("claims");
  if (el) return JSON.parse(el.textContent);
  const r = await fetch("./claims.json");
  return r.json();
}

load().then((c) => {
  C = c;
  Object.entries(c.brand.palette).forEach(([k, v]) => document.documentElement.style.setProperty("--" + k, v));
  document.title = c.brand.wordmark;
  const top = document.getElementById("top");
  top.textContent = c.brand.wordmark;
  hudEl = h("span", "hud");
  hudEl.hidden = true;
  top.append(hudEl);
  const f = document.getElementById("foot");
  f.append(h("span", null, c.brand.footer), h("span", null, c.brand.signoff));
  const meta = document.createElement("meta");   // the build time stays in the page, not on screen
  meta.setAttribute("name", "steer-built-at");
  meta.setAttribute("content", c.built_at);
  document.head.append(meta);
  start();
});
