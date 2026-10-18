// Renders a reel_props.json with the "Reel" composition. Called by `steer reel` (Python), which validates the props
// against props.schema.json and checks every string before this runs, then encodes the audio and runs the QA.
//
//   node render.mjs <publicDir> <props.json> <out.mkv>
//
// <publicDir> holds the voice WAV and the font files the props name. The output is H.264 (yuv420p) with PCM audio
// in an MKV: encoding AAC once afterwards (in Python, from the same timeline) avoids the AAC encoder's start-up
// padding in front of the sound (the h264-mkv path of ai-video-editor-cooked's render.mjs, MIT).
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { bundle } from "@remotion/bundler";
import { renderMedia, selectComposition } from "@remotion/renderer";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [publicDir, propsPath, out] = process.argv.slice(2);
if (!publicDir || !propsPath || !out) {
  console.error("usage: node render.mjs <publicDir> <props.json> <out.mkv>");
  process.exit(2);
}
const inputProps = JSON.parse(fs.readFileSync(propsPath, "utf8"));
const t0 = Date.now();
const serveUrl = await bundle({ entryPoint: path.join(HERE, "src", "index.ts"), publicDir: path.resolve(publicDir) });
process.on("exit", () => fs.rmSync(serveUrl, { recursive: true, force: true }));  // the bundle copies the public folder
const bundled = (Date.now() - t0) / 1000;
const composition = await selectComposition({ serveUrl, id: "Reel", inputProps });
let shown = -1;
await renderMedia({
  serveUrl, composition, inputProps, codec: "h264-mkv", outputLocation: out, pixelFormat: "yuv420p",
  enforceAudioTrack: true, timeoutInMilliseconds: 120000,
  onProgress: ({ renderedFrames }) => {
    if (renderedFrames - shown >= 150 || renderedFrames === composition.durationInFrames) {
      shown = renderedFrames;
      console.log(`Rendered ${renderedFrames}/${composition.durationInFrames}`);
    }
  },
});
console.log(JSON.stringify({ bundle_s: bundled, render_s: (Date.now() - t0) / 1000 - bundled,
  frames: composition.durationInFrames }));
