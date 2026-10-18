// LineArtTest trial: node render-lineart.mjs <outDir>  (gl pinned to "angle": determinism verified there)
// Renders frame 45 twice (sha256 compare) and the 90-frame draw-on to <outDir>/drawon.mp4.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { renderMedia, renderStill, selectComposition } from "@remotion/renderer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const gl = "angle", [outDir = "../../trials/lineart-1"] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const pub = fs.mkdtempSync(path.join(os.tmpdir(), "lineart-pub-"));
for (const [src, dst] of [["vendor/p5brush/brush.js", "brush.js"], ["lineart/draw.js", "draw.js"], ["lineart/chai.json", "chai.json"]])
  fs.copyFileSync(path.join(HERE, "..", src), path.join(pub, dst));
const serveUrl = await bundle({ entryPoint: path.join(HERE, "src", "index.ts"), publicDir: pub });
process.on("exit", () => { fs.rmSync(serveUrl, { recursive: true, force: true }); fs.rmSync(pub, { recursive: true, force: true }); });
const chromiumOptions = { gl };
const composition = await selectComposition({ serveUrl, id: "LineArtTest", chromiumOptions });
const sha = (f) => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
const hashes = [];
for (const k of ["a", "b"]) {
  const output = path.join(outDir, `frame45-${k}.png`);
  await renderStill({ serveUrl, composition, frame: 45, output, chromiumOptions });
  hashes.push(sha(output));
}
console.log(JSON.stringify({ gl, hashA: hashes[0], hashB: hashes[1], match: hashes[0] === hashes[1] }));
const t0 = Date.now();
await renderMedia({ serveUrl, composition, codec: "h264", outputLocation: path.join(outDir, "drawon.mp4"),
  pixelFormat: "yuv420p", chromiumOptions, timeoutInMilliseconds: 120000, concurrency: 1 });
const s = (Date.now() - t0) / 1000;
console.log(JSON.stringify({ render_s: s, ms_per_frame: (s * 1000) / composition.durationInFrames }));
