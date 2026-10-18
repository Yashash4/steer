// Draws only strings from claims.json. Only computed numbers: readout counts n and m.
const main = document.querySelector("main");
const COMMA = String.fromCharCode(44);
const COLON = String.fromCharCode(58);
const DOT = String.fromCharCode(183);
const NL = String.fromCharCode(10);
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

// slider over stops; onChange(index) fires on every move
function slider(round, onChange) {
  const long = round.stops.some((s) => s.label.length > 5);
  const track = h("div", ["slider", long ? "vert" : "horiz"]);
  const current = h("div", "current");
  const stops = round.stops.map((s) => {
    const st = h("div", "stop");
    st.append(h("span", "dot"), h("span", "slabel", s.label));
    return st;
  });
  track.append(...stops);
  track.setAttribute("role", "slider");
  track.setAttribute("tabindex", "0");
  track.setAttribute("aria-orientation", long ? "vertical" : "horizontal");
  track.setAttribute("aria-valuemin", "0");
  track.setAttribute("aria-valuemax", String(stops.length - 1));
  track.setAttribute("aria-label", round.question);
  let sel = -1;
  const set = (i) => {
    i = Math.max(0, Math.min(stops.length - 1, i));
    sel = i;
    stops.forEach((st, k) => st.classList.toggle("on", k === i));
    track.setAttribute("aria-valuenow", String(i));
    track.setAttribute("aria-valuetext", round.stops[i].label);
    current.textContent = round.stops[i].label;
    onChange(i);
  };
  const near = (e) => {
    let best = 0;
    let bd = Infinity;
    stops.forEach((st, k) => {
      const r = st.getBoundingClientRect();
      const d = long ? Math.abs(e.clientY - (r.top + r.height / 2)) : Math.abs(e.clientX - (r.left + r.width / 2));
      if (d < bd) { bd = d; best = k; }
    });
    set(best);
  };
  track.addEventListener("pointerdown", (e) => {
    try { track.setPointerCapture(e.pointerId); } catch (x) { /* synthetic pointer */ }
    near(e);
  });
  track.addEventListener("pointermove", (e) => { if (e.buttons) near(e); });
  track.addEventListener("keydown", (e) => {
    const k = e.key.toLowerCase();
    const up = k === "arrowright" || k === "arrowup";
    const down = k === "arrowleft" || k === "arrowdown";
    if (!(up || down || k === "home" || k === "end")) return;
    e.preventDefault();
    if (k === "home") set(0);
    else if (k === "end") set(stops.length - 1);
    else if (sel < 0) set(up ? 0 : stops.length - 1);
    else set(sel + (up ? 1 : -1));
  });
  const wrap = h("div", "slider-wrap");
  wrap.append(current, track);
  return wrap;
}

function ask(round, done, n) {
  const nodes = [progress(n), h("p", "opener", round.opener)];
  nodes.push(h("h1", "q", round.question));
  if (round.context) nodes.push(h("p", ["context", "small"], round.context));
  if (round.prompt) nodes.push(h("p", "prompt", round.prompt));
  if (round.kind === "choice") {
    const box = h("div", "choices");
    round.stops.forEach((s, i) => {
      const b = h("button", "choice", s.label);
      b.type = "button";
      b.addEventListener("click", () => done(i));
      box.append(b);
    });
    nodes.push(box);
  } else {
    let pick = -1;
    const lock = button(C.ui.lock, () => done(pick));
    lock.disabled = true;
    nodes.push(slider(round, (i) => { pick = i; lock.disabled = false; }), lock);
  }
  show(...nodes);
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

function reveal(round, idx, next) {
  const nodes = [];
  round.reveals.forEach((rv) => {
    const card = h("div", "reveal");
    const row = h("div", "numrow");
    // a round with no number draws its label large instead
    if (rv.number) row.append(h("span", "num", rv.number), h("span", "rlabel", rv.label));
    else row.append(h("span", ["rlabel", "lead"], rv.label));
    card.append(row, h("p", "sentence", rv.sentence));
    nodes.push(card);
  });
  const g = h("p", "guess");
  g.append(h("span", null, C.ui.you_guessed), h("b", null, round.stops[idx].label));
  nodes.push(g);
  const ul = qualList(round);
  if (ul.children.length) nodes.push(ul);
  const src = h("p", "src");
  src.append(h("span", null, round.source.short), h("span", null, round.source.location));
  nodes.push(src);
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
  nodes.push(tog, panel, button(C.ui.next, next));
  show(...nodes);
}

function playRound(round, next, n) {
  ask(round, (idx) => {
    S.guesses.push({ round, idx });
    reveal(round, idx, next);
  }, n);
}

// an info card, not a guess: nothing is asked and nothing enters S.guesses
function gapCard(card, next, n) {
  const c = h("div", ["card", "gap"]);
  if (card.context) c.append(h("p", "context", card.context));
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

// a choice with numeric stops is a bin scale (value = lower end of the bin): scored by the bin the answer falls in
const isNumber = (g) => typeof g.round.stops[0].value === "number";
function score(g) {
  const a = g.round.answer;
  const v = g.round.stops[g.idx].value;
  if (g.round.kind === "choice" && isNumber(g)) {
    const bin = g.round.stops.filter((s) => s.value <= a).length - 1;
    return g.idx < bin ? "under" : g.idx > bin ? "over" : "close";
  }
  if (g.round.kind === "choice") return v === a ? "close" : "under";
  if (v < a * 0.9) return "under";
  if (v > a * 1.1) return "over";
  return "close";
}
function questions() {
  const qs = [];
  S.picked.map((d) => d.id).concat("is_it_for_me").forEach((id) => (C.doctor_questions[id] || []).forEach((q) => qs.push(q)));
  return qs.slice(0, 3);
}

function readout() {
  const nodes = [h("h1", "q", C.ui.readout_title)];
  const nums = S.guesses.filter((g) => g.round.kind === "slider" || isNumber(g));
  const dirs = S.guesses.filter((g) => g.round.kind === "choice" && !isNumber(g));
  const card = h("div", "card");
  if (nums.length) {
    const cnt = { under: 0, over: 0, close: 0 };
    nums.forEach((g) => { cnt[score(g)] += 1; });
    const top = ["under", "over", "close"].reduce((a, b) => (cnt[b] > cnt[a] ? b : a));
    card.append(h("p", "big", fill(C.readout[top], cnt[top], nums.length)));
  }
  if (dirs.length) {
    const right = dirs.filter((g) => score(g) === "close").length;
    card.append(h("p", "big", fill(C.readout.direction, right, dirs.length)));
  }
  if (card.children.length) nodes.push(card);
  const mean = h("div", "card");
  mean.append(h("h2", null, C.readout.meaning_title));
  const ul = h("ul", "list");
  S.guesses.forEach((g) => {
    const li = h("li");
    g.round.reveals.forEach((rv) => li.append(h("p", null, rv.sentence)));
    li.append(qualList(g.round, ["quals", "tiny"]), h("p", "small", g.round.source.short));
    ul.append(li);
  });
  mean.append(ul);
  nodes.push(mean);
  const qs = questions();
  const qc = h("div", "card");
  qc.append(h("h2", null, C.ui.questions_title));
  const ql = h("ul", "list");
  qs.forEach((q) => ql.append(h("li", null, q)));
  qc.append(ql);
  nodes.push(qc, button(C.ui.next, nextStep));
  show(...nodes);
}

function link(l, cls) {
  const a = h("a", ["btn"].concat(cls || []), l.label);
  a.href = l.url; // exactly as given, nothing appended
  return a;
}

function copyText(text, status) {
  const done = () => { status.textContent = C.warm_lead.copied; };
  const old = () => {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.append(ta);
    ta.select();
    try { document.execCommand("copy"); done(); } catch (x) { /* nothing to do */ }
    ta.remove();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, old);
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
  const status = h("span", "small");
  status.setAttribute("role", "status");
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
  const cp = button(w.copy_label, () => copyText(text(), status), "alt");
  const sh = button(w.share_label, () => {
    if (navigator.share) navigator.share({ title: w.share_title, text: text() }).catch(() => {});
    else copyText(text(), status);
  }, "alt");
  card.append(nl, pl, cl, showBtn, prev, cp, sh, status);
  return card;
}

function nextStep() {
  show(link(C.cta.primary), link(C.cta.secondary, "alt"), warmLead(), button(C.ui.restart, start, "alt"));
}

function start() {
  S = { picked: [], guesses: [], total: 0 };
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
  document.getElementById("top").textContent = c.brand.wordmark;
  const f = document.getElementById("foot");
  f.append(h("span", null, c.brand.footer), h("span", null, c.brand.signoff));
  const meta = document.createElement("meta");   // the build time stays in the page, not on screen
  meta.setAttribute("name", "steer-built-at");
  meta.setAttribute("content", c.built_at);
  document.head.append(meta);
  start();
});
