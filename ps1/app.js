// Draws only strings from claims.json. Only computed numbers: readout counts n and m.
const main = document.querySelector("main");
const COMMA = String.fromCharCode(44);
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

function ask(round, done) {
  const nodes = [];
  if (round.context) nodes.push(h("p", "context", round.context));
  nodes.push(h("h1", "q", round.question));
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

function reveal(round, idx, next) {
  const nodes = [];
  round.reveals.forEach((rv) => {
    const card = h("div", "reveal");
    const row = h("div", "numrow");
    row.append(h("span", "num", rv.number), h("span", "rlabel", rv.label));
    card.append(row, h("p", "sentence", rv.sentence));
    nodes.push(card);
  });
  const g = h("p", "guess");
  g.append(h("span", null, C.ui.you_guessed), h("b", null, round.stops[idx].label));
  nodes.push(g);
  const ul = h("ul", "quals");
  round.qualifiers.forEach((q) => ul.append(h("li", null, q)));
  if (C.ui.reveal_note) ul.append(h("li", null, C.ui.reveal_note));   // never personal risk
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

function playRound(round, next) {
  ask(round, (idx) => {
    S.guesses.push({ round, idx });
    reveal(round, idx, next);
  });
}

function gapCard(card, next) {
  const c = h("div", ["card", "gap"]);
  c.append(h("h2", null, card.title), h("p", null, card.body));
  show(c, button(C.ui.next, next));
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
  const go = (k) => {
    if (k >= steps.length) return readout();
    const st = steps[k];
    if (st.round) playRound(st.round, () => go(k + 1));
    else gapCard(st.card, () => go(k + 1));
  };
  go(i);
}

function score(g) {
  const a = g.round.answer;
  const v = g.round.stops[g.idx].value;
  if (g.round.kind === "choice") return v === a ? "close" : "under";
  if (v < a * 0.9) return "under";
  if (v > a * 1.1) return "over";
  return "close";
}
function fill(t, n, m) { return t.replace("{n}", String(n)).replace("{m}", String(m)); }

function readout() {
  const nodes = [h("h1", "q", C.ui.readout_title)];
  const nums = S.guesses.filter((g) => g.round.kind === "slider");
  const dirs = S.guesses.filter((g) => g.round.kind === "choice");
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
  S.guesses.forEach((g) => g.round.reveals.forEach((rv) => ul.append(h("li", null, rv.sentence))));
  mean.append(ul);
  nodes.push(mean);
  const qs = [];
  S.picked.map((d) => d.id).concat("is_it_for_me").forEach((id) => (C.doctor_questions[id] || []).forEach((q) => qs.push(q)));
  const qc = h("div", "card");
  qc.append(h("h2", null, C.ui.questions_title));
  const ql = h("ul", "list");
  qs.slice(0, 3).forEach((q) => ql.append(h("li", null, q)));
  qc.append(ql);
  nodes.push(qc, button(C.ui.next, nextStep));
  show(...nodes);
}

function link(l, cls) {
  const a = h("a", ["btn"].concat(cls || []), l.label);
  a.href = l.url; // exactly as given, nothing appended
  return a;
}

function nextStep() {
  const w = C.warm_lead;
  const card = h("div", ["card", "warm"]);
  card.append(h("h2", null, w.title), h("p", "small", w.note), h("h3", null, w.doubts_label));
  const tags = h("div", "tags");
  S.picked.forEach((d) => tags.append(h("span", "tag", d.label)));
  card.append(tags, h("h3", null, w.guesses_label));
  const ul = h("ul", "list");
  S.guesses.forEach((g) => {
    const li = h("li");
    li.append(h("b", null, g.round.stops[g.idx].label));
    g.round.reveals.forEach((rv) => {
      const row = h("div", "row");
      row.append(h("span", "num", rv.number), h("span", "rlabel", rv.label));
      li.append(row);
    });
    ul.append(li);
  });
  card.append(ul);
  show(link(C.cta.primary), link(C.cta.secondary, "alt"), card, button(C.ui.restart, start, "alt"));
}

function start() {
  S = { picked: [], guesses: [] };
  playRound(C.hook, picker);
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
