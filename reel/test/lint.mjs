// Accuracy lint: node test/lint.mjs [extra.tsx ...]. Exit 1 on any violation.
// Checks (TypeScript AST, so it is exact): no JSX text / string children, no number formatting or animation,
// and every schema key that is drawn or used for timing appears in src.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const walk = (d) => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) =>
  e.isDirectory() ? walk(path.join(d, e.name)) : [path.join(d, e.name)]);
const srcFiles = walk(path.join(ROOT, "src"));
const tsx = [...srcFiles.filter((f) => f.endsWith(".tsx")), ...process.argv.slice(2)];
const problems = [];

const FORBIDDEN = [/toFixed/, /toLocaleString/, /Intl\.NumberFormat/, /counter\(/, /counter-increment/, /@property/, /\bString\(/];
const strip = (s) => s.replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");

for (const file of tsx) {
  const text = fs.readFileSync(file, "utf8");
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
  const at = (n) => `${path.relative(ROOT, file)}:${sf.getLineAndCharacterOfPosition(n.getStart()).line + 1}`;
  const isLit = (n) => ts.isStringLiteralLike(n) || ts.isTemplateExpression(n);
  const visit = (n) => {
    if (ts.isJsxText(n) && /[\p{L}\p{N}]/u.test(n.text)) problems.push(`${at(n)}: JSX text "${n.text.trim()}"`);
    if (ts.isJsxExpression(n) && n.expression && (ts.isJsxElement(n.parent) || ts.isJsxFragment(n.parent))) {
      // strings that would be rendered: the expression itself, a branch of ?: / && / || / ??, or a .map(...) body
      const lits = [];
      const find = (m) => {
        if (isLit(m)) lits.push(m);
        else if (ts.isParenthesizedExpression(m)) find(m.expression);
        else if (ts.isConditionalExpression(m)) { find(m.whenTrue); find(m.whenFalse); }
        else if (ts.isBinaryExpression(m)) { find(m.left); find(m.right); }
        else if (ts.isCallExpression(m)) m.arguments.forEach((a) => ts.isArrowFunction(a) && !ts.isBlock(a.body) && find(a.body));
      };
      find(n.expression);
      for (const l of lits) problems.push(`${at(l)}: string literal in JSX children`);
      const uses = (m) => (ts.isIdentifier(m) && /frame/i.test(m.text)) || ts.forEachChild(m, uses);
      if (ts.isTemplateExpression(n.expression) && uses(n.expression)) problems.push(`${at(n)}: frame in template child`);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  strip(text).split("\n").forEach((l, i) => {
    for (const re of FORBIDDEN) if (re.test(l)) problems.push(`${path.relative(ROOT, file)}:${i + 1}: forbidden ${re}`);
  });
}

const schema = JSON.parse(fs.readFileSync(path.join(ROOT, "props.schema.json"), "utf8"));
const d = schema.$defs;
const keys = new Set([
  ...Object.keys(schema.properties.chrome.properties), ...Object.keys(d.line.properties),
  ...Object.keys(d.line.properties.key.oneOf[1].properties), ...Object.keys(d.sources.properties),
  ...Object.keys(d.sources.properties.entries.items.properties), ...Object.keys(d.line.properties.beats.items.properties),
  ...Object.keys(d.line.properties.beats.items.properties.words.items.properties),
]);
const all = srcFiles.filter((f) => /\.tsx?$/.test(f) && !f.endsWith("types.ts")).map((f) => strip(fs.readFileSync(f, "utf8"))).join("\n");
for (const k of keys) if (!new RegExp(`\\.${k}\\b`).test(all)) problems.push(`schema key never used in src: ${k}`);

if (problems.length) { console.error(problems.join("\n")); process.exit(1); }
console.log(`lint ok: ${tsx.length} tsx files, ${keys.size} schema keys used`);
