import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
const [claims, out] = process.argv.slice(2);
if (!claims || !out) { console.error("usage: node build_playable.mjs <claims.json> <out.html>"); process.exit(2); }
const rd = (f) => readFileSync(join(here, f), "utf8");
const json = JSON.stringify(JSON.parse(readFileSync(claims, "utf8"))).replace(/</g, "\\u003c");
let css = rd("style.css").replace(/url\("(fonts\/[^"]+)"\)/g, (_, p) => `url(data:font/ttf;base64,${readFileSync(join(here, p)).toString("base64")})`);
const js = rd("app.js").replace(/<\/script/gi, "<\\/script");
const html = rd("index.html")
  .replace('<link rel="stylesheet" href="style.css">', () => `<style>${css}</style>`)
  .replace('<script src="app.js" defer></script>', () => `<script type="application/json" id="claims">${json}</script>\n<script>${js}</script>`);
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, html);
console.log(out, Math.round(Buffer.byteLength(html) / 1024) + " KB");
