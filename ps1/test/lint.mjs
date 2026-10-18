// node test/lint.mjs [app.js path]  -> exit 1 on failure
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const ts = createRequire(import.meta.url)("../../reel/node_modules/typescript");
const fails = [];
const fail = (m) => fails.push(m);

// (a) index.html: no visible text in body
const html = readFileSync(join(root, "index.html"), "utf8");
const body = (html.match(/<body[^>]*>([\s\S]*)<\/body>/i) || [, ""])[1]
  .replace(/<script[\s\S]*?<\/script>/gi, "").replace(/<style[\s\S]*?<\/style>/gi, "")
  .replace(/<!--[\s\S]*?-->/g, "").replace(/<[^>]*>/g, "").trim();
if (body) fail("index.html body has visible text: " + body.slice(0, 40));

// (b) app.js string literals
const appPath = process.argv[2] || join(root, "app.js");
const src = readFileSync(appPath, "utf8");
const sf = ts.createSourceFile(appPath, src, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
const SAFE_CALLS = new Set(["createElement", "createElementNS", "setAttribute", "getAttribute", "removeAttribute", "hasAttribute",
  "add", "remove", "toggle", "contains", "addEventListener", "removeEventListener", "querySelector", "querySelectorAll",
  "closest", "matches", "getElementById", "setProperty", "getPropertyValue", "fetch", "require"]);
const calleeName = (c) => ts.isIdentifier(c.expression) ? c.expression.text : ts.isPropertyAccessExpression(c.expression) ? c.expression.name.text : "";
function exempt(n) {
  const p = n.parent;
  if (!p) return false;
  if (ts.isCallExpression(p) && p.arguments.includes(n) && SAFE_CALLS.has(calleeName(p))) return true;
  if (ts.isElementAccessExpression(p) && p.argumentExpression === n) return true; // dataset["k"], style["k"]
  if (ts.isPropertyAssignment(p) && p.name === n) return true; // object key
  if (ts.isImportDeclaration(p) || ts.isExpressionStatement(p)) return true; // directives
  return false;
}
const bad = (t) => /[A-Z]/.test(t) || /[A-Za-z] [A-Za-z]/.test(t) || /[?!.,]/.test(t);
(function walk(n) {
  const isStr = ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n);
  const isTpl = ts.isTemplateExpression(n);
  if ((isStr || isTpl) && !exempt(n)) {
    const text = isStr ? n.text : n.getText().slice(1, -1);
    const sel = text.startsWith(".") || text.startsWith("#");
    const line = sf.getLineAndCharacterOfPosition(n.getStart()).line + 1;
    if (isTpl) fail(`${appPath}:${line} template with substitutions could be shown as text`);
    else if (!sel && bad(text)) fail(`${appPath}:${line} text-like literal ${JSON.stringify(text)}`);
  }
  ts.forEachChild(n, walk);
})(sf);

// (c) forbidden APIs
const css = readFileSync(join(root, "style.css"), "utf8");
for (const [name, text] of [["app.js", src], ["style.css", css], ["index.html", html]]) {
  for (const w of ["toFixed", "toLocaleString", "Intl.NumberFormat", "counter(", "@property"])
    if (text.includes(w)) fail(`${name} contains ${w}`);
  if (/XMLHttpRequest|sendBeacon|WebSocket|EventSource/.test(text)) fail(`${name} uses a network API`);
}
const fetches = src.match(/\bfetch\s*\(/g) || [];
if (fetches.length > 1 || (fetches.length === 1 && !/fetch\(\s*"\.\/claims\.json"\s*\)/.test(src)))
  fail("fetch is only allowed once, for ./claims.json");

// (d) no absolute URLs
for (const [name, text] of [["index.html", html], ["app.js", src], ["style.css", css]])
  if (/https?:\/\//.test(text)) fail(`${name} contains an absolute url`);

if (fails.length) { console.error(fails.join("\n")); process.exit(1); }
console.log("lint ok");
