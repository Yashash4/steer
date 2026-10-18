// Meaning safety, checked on every frame: node test/beats.mjs (exit 1 on failure).
// At every frame the visible text is a prefix of the line made of whole phrases, and a framing line is complete
// from its first frame. The health line is the real West reel line whose negation once scrolled away
// (trials/s7-reel-2, scene 3); the framing line is the s7-reel-3 hook that read "back when the medicine / stops?".
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import ts from "typescript";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const js = ts.transpileModule(fs.readFileSync(path.join(ROOT, "src", "beats.ts"), "utf8"),
  { compilerOptions: { module: ts.ModuleKind.ES2022, target: ts.ScriptTarget.ES2022 } }).outputText;
const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "steer-beats-")), "beats.mjs");
fs.writeFileSync(tmp, js);
const { visibleTexts, cardsShown } = await import(pathToFileURL(tmp).href);

const LINE = {"kind": "health", "text": "No evidence was found that the intensity of behavioural support during treatment changed the rate of weight regain after treatment.", "start": 12.624, "end": 20.304, "hold_end": 21.104, "beats": [{"text": "No evidence was found", "start": 12.72, "words": [{"text": "No", "start": 12.72}, {"text": "evidence", "start": 13.01}, {"text": "was", "start": 13.507}, {"text": "found", "start": 13.681}]}, {"text": "that the intensity of", "start": 13.993, "words": [{"text": "that", "start": 13.993}, {"text": "the", "start": 14.167}, {"text": "intensity", "start": 14.283}, {"text": "of", "start": 14.896}]}, {"text": "behavioural support during treatment", "start": 15.035, "words": [{"text": "behavioural", "start": 15.035}, {"text": "support", "start": 15.602}, {"text": "during", "start": 16.042}, {"text": "treatment", "start": 16.331}]}, {"text": "changed the rate of", "start": 16.852, "words": [{"text": "changed", "start": 16.852}, {"text": "the", "start": 17.292}, {"text": "rate", "start": 17.396}, {"text": "of", "start": 17.686}]}, {"text": "weight regain after treatment.", "start": 17.848, "words": [{"text": "weight", "start": 17.848}, {"text": "regain", "start": 18.114}, {"text": "after", "start": 18.542}, {"text": "treatment.", "start": 18.855}]}], "emphasis": null, "key": null, "source_label": "West et al. 2026, section text, no page layout"};
const HOOK = {"kind": "framing", "text": "What happens to your weight back when the medicine stops?", "start": 0.4, "end": 3.1, "hold_end": 3.7,
  "beats": [{"text": "What happens to your", "start": 0.45, "words": []}, {"text": "weight back when the", "start": 1.4, "words": []},
            {"text": "medicine stops?", "start": 2.3, "words": []}], "emphasis": null, "key": null, "source_label": null};

// the check: the shown phrases, joined, are the line itself or a prefix of it ending on a phrase boundary
const prefixes = (line) => line.beats.map((_, k) => line.beats.slice(0, k + 1).map((b) => b.text).join(" "));
const check = (line, show, from, full) => {
  const ok = new Set(full ? [line.text] : prefixes(line));
  const bad = [];
  let n = 0;
  for (let f = Math.ceil(from * 30); f / 30 < line.hold_end; f++, n++) {
    const t = f / 30, texts = show(line, t);
    if (!ok.has(texts.join(" "))) bad.push(`t=${t.toFixed(3)}: ${JSON.stringify(texts)}`);
  }
  return { bad, n };
};
const problems = [];
const health = check(LINE, (l, t) => visibleTexts(l, t), LINE.start, false);
const hook = check(HOOK, (l, t) => visibleTexts(l, t, 0), 0, true);
problems.push(...health.bad, ...hook.bad);
// the proof the check bites: v2's drop-out (only the current beat on screen) fails it, on both lines
const dropOut = (l, t) => { let i = 0; l.beats.forEach((b, k) => { if (b.start <= t) i = k; }); return [l.beats[i].text]; };
if (!check(LINE, dropOut, LINE.beats[1].start, false).bad.length) problems.push("the check passes v2's drop-out on the health line");
if (!check(HOOK, dropOut, HOOK.start, true).bad.length) problems.push("the check passes a partial hook question");
// a 2-card line (the c004 line, as reel.py make_cards splits it): every frame shows card 1 alone or both cards whole
const CARDS = {"kind": "health", "text": "Aged 45+ in Chennai, Delhi and Karachi, with body mass index under 25\u00a0kg/m²: diabetes risk was 5.27\u00a0times higher in South Asian women and 2.99\u00a0times in men than US whites.",
  "start": 4.2, "end": 19.5, "hold_end": 20.3, "beats": [], "cards": [
  {"start": 4.25, "parts": [{"text": "Aged 45+ in Chennai, Delhi and Karachi, with body mass index under 25\u00a0kg/m²:", "role": "setting", "show": "", "at": 4.25}]},
  {"start": 10.6, "parts": [{"text": "diabetes risk was", "role": "text", "at": 10.6}, {"text": "5.27\u00a0times", "role": "hero", "at": 11.5},
    {"text": "higher in South Asian women and", "role": "text", "at": 12.2}, {"text": "2.99\u00a0times", "role": "hero", "at": 14.6},
    {"text": "in men than US whites.", "role": "text", "at": 15.3}]}]};
if ([...CARDS.cards[0].parts, ...CARDS.cards[1].parts].map((q) => q.text).join(" ") !== CARDS.text) problems.push("the cards do not cover the line");
let cardFrames = 0;
for (let f = Math.ceil(CARDS.start * 30); f / 30 < CARDS.hold_end; f++, cardFrames++) {
  const t = f / 30, n = cardsShown(CARDS, t);
  const want = t < CARDS.cards[1].start ? 1 : 2;   // card 2 never without card 1 (the setting stays as a strip)
  if (n !== want) problems.push(`t=${t.toFixed(3)}: ${n} cards on screen, want ${want}`);
}
if (cardsShown(CARDS, CARDS.hold_end) !== null || cardsShown(CARDS, CARDS.start - 0.01) !== null) problems.push("cards outside the line");
if (problems.length) { console.error(problems.slice(0, 10).join("\n")); process.exit(1); }
console.log(`beats ok: health line ${health.n} frames, each a whole-phrase prefix; hook ${hook.n} frames, complete from frame 0; ` +
  `v2's drop-out fails the check; cards ${cardFrames} frames, the setting on every one, card 2 whole`);
