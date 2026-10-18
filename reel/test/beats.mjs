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
const { visibleTexts } = await import(pathToFileURL(tmp).href);

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
if (problems.length) { console.error(problems.slice(0, 10).join("\n")); process.exit(1); }
console.log(`beats ok: health line ${health.n} frames, each a whole-phrase prefix; hook ${hook.n} frames, complete from frame 0; ` +
  "v2's drop-out fails the check");
