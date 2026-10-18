// Meaning safety and no overlap, checked on every frame: node test/beats.mjs (exit 1 on failure).
// The line is the real West reel line whose negation once scrolled away (trials/s7-reel-2, scene 3).
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
const { onScreen, visibleTexts, pinnedSet, EXIT_S, ENTER_DELAY } = await import(pathToFileURL(tmp).href);

const LINE = {"kind": "health", "text": "No evidence was found that the intensity of behavioural support during treatment changed the rate of weight regain after treatment.", "start": 12.624, "end": 20.304, "hold_end": 21.104, "beats": [{"text": "No evidence was found", "start": 12.72, "words": [{"text": "No", "start": 12.72}, {"text": "evidence", "start": 13.01}, {"text": "was", "start": 13.507}, {"text": "found", "start": 13.681}]}, {"text": "that the intensity of", "start": 13.993, "words": [{"text": "that", "start": 13.993}, {"text": "the", "start": 14.167}, {"text": "intensity", "start": 14.283}, {"text": "of", "start": 14.896}]}, {"text": "behavioural support during treatment", "start": 15.035, "words": [{"text": "behavioural", "start": 15.035}, {"text": "support", "start": 15.602}, {"text": "during", "start": 16.042}, {"text": "treatment", "start": 16.331}]}, {"text": "changed the rate of", "start": 16.852, "words": [{"text": "changed", "start": 16.852}, {"text": "the", "start": 17.292}, {"text": "rate", "start": 17.396}, {"text": "of", "start": 17.686}]}, {"text": "weight regain after treatment.", "start": 17.848, "words": [{"text": "weight", "start": 17.848}, {"text": "regain", "start": 18.114}, {"text": "after", "start": 18.542}, {"text": "treatment.", "start": 18.855}]}], "emphasis": null, "key": null, "source_label": "West et al. 2026, section text, no page layout"};
const problems = [];
const frames = [];
for (let f = Math.ceil(LINE.start * 30); f / 30 < LINE.hold_end; f++) frames.push(f / 30);
for (const t of frames) {
  const texts = visibleTexts(LINE, t);
  if (!texts.some((x) => x.includes("No evidence"))) problems.push(`t=${t.toFixed(3)}: "No evidence" not on screen: ${JSON.stringify(texts)}`);
  const s = onScreen(LINE, t);
  // never two moving beats visible together: the exiting one is gone before the next one starts to appear
  if (s && s.exiting !== null && t - LINE.beats[s.current].start >= Math.min(EXIT_S, ENTER_DELAY))
    problems.push(`t=${t.toFixed(3)}: beat ${s.exiting} still leaving while ${s.current} appears`);
}
const pins = [...pinnedSet(LINE)];
if (!pins.includes(0)) problems.push("the opening beat is not pinned");
// a qualifier later in a line is pinned too; a framing line pins nothing
const q = { ...LINE, beats: [...LINE.beats, { text: "only in this study", start: LINE.end - 0.5, words: [] }] };
if (!pinnedSet(q).has(q.beats.length - 1)) problems.push("a qualifier beat is not pinned");
if (pinnedSet({ ...LINE, kind: "framing" }).size) problems.push("a framing line pins beats");
// at most 2 pinned blocks + the current beat at every frame; with a key pair on screen, at most 1 pinned beat
const many = { ...LINE, beats: LINE.beats.map((b, k) => (k % 2 ? { ...b, text: b.text + " may" } : b)) };
for (let f = Math.ceil(many.start * 30); f / 30 < many.end; f++) {
  const t = f / 30;
  const s = onScreen(many, t), sk = onScreen(many, t, true);
  if (s.pinned.length > 2) problems.push(`t=${t.toFixed(3)}: ${s.pinned.length} pinned beats`);
  if (sk.pinned.length > 1) problems.push(`t=${t.toFixed(3)}: ${sk.pinned.length} pinned beats beside a key pair`);
  if (s.pinned.length && s.pinned[0] !== 0) problems.push(`t=${t.toFixed(3)}: the opening beat is not the first pinned`);
  if (!visibleTexts(many, t, true).some((x) => x.includes("No evidence"))) problems.push(`t=${t.toFixed(3)}: key scene lost "No evidence"`);
}
const last = onScreen(many, many.end - 0.01);
const quals = [...pinnedSet(many)].filter((k) => k !== 0 && k < last.current);
if (last.pinned[1] !== quals[quals.length - 1]) problems.push("the pinned qualifier is not the most recent one");
if (problems.length) { console.error(problems.slice(0, 10).join("\n")); process.exit(1); }
console.log(`beats ok: ${frames.length} frames, "No evidence" on screen in every one; pinned ${JSON.stringify(pins)}; ` +
  "at most 2 pinned (1 beside a key pair) on every frame");
