import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const [claims, out, from] = process.argv.slice(2);
if (!claims || !out) { console.error("usage: node build_playable.mjs <claims.json> <out.html> [<site folder>]"); process.exit(2); }
// the page files and fonts come from the site folder (steer ps1 build's output), else from this folder
const here = from || dirname(fileURLToPath(import.meta.url));
const rd = (f) => readFileSync(join(here, f), "utf8");
const json = JSON.stringify(JSON.parse(readFileSync(claims, "utf8"))).replace(/</g, "\\u003c");
const mime = (p) => (p.endsWith(".woff2") ? "font/woff2" : "font/ttf");
let css = rd("style.css").replace(/url\("(fonts\/[^"]+)"\)/g, (_, p) => `url(data:${mime(p)};base64,${readFileSync(join(here, p)).toString("base64")})`);
const js = rd("app.js").replace(/<\/script/gi, "<\\/script");
const html = rd("index.html")
  .replace('<link rel="stylesheet" href="style.css">', () => `<style>${css}</style>`)
  .replace('<script src="app.js" defer></script>', () => `<script type="application/json" id="claims">${json}</script>\n<script>${js}</script>`);
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, html);
console.log(out, Math.round(Buffer.byteLength(html) / 1024) + " KB");
