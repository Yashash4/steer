// Test composition: stroke-by-stroke draw-on of lineart/chai.json with p5.brush. Frame-deterministic: every frame
// clears and redraws from scratch (draw.js seeds the brush each call), so nothing carries between frames.
// brush.js, draw.js and chai.json come from the public dir (render-lineart.mjs copies them there).
import { useEffect, useRef } from "react";
import { continueRender, delayRender, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

type Brush = { createCanvas: (w: number, h: number, o: object) => HTMLCanvasElement; scaleBrushes: (n: number) => void };
const g = globalThis as unknown as { brush: Brush; drawChai: (...a: unknown[]) => void };
let ready: Promise<unknown> | null = null;
let canvas: HTMLCanvasElement | null = null;

const script = (src: string) =>
  new Promise<void>((ok, bad) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => ok();
    s.onerror = () => bad(new Error(src));
    document.head.appendChild(s);
  });

export const LineArt = () => {
  const frame = useCurrentFrame();
  const { width, height, durationInFrames } = useVideoConfig();
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const h = delayRender("lineart");
    ready ??= Promise.all([script(staticFile("brush.js")), fetch(staticFile("chai.json")).then((r) => r.json())])
      .then(async ([, chai]) => { await script(staticFile("draw.js")); return chai; });
    ready.then((chai) => {
      if (!canvas) {
        canvas = g.brush.createCanvas(width, height, { parent: host.current!, pixelDensity: 1 });
        g.brush.scaleBrushes(2);
      }
      if (canvas.parentElement !== host.current) host.current!.appendChild(canvas);
      const p = interpolate(frame, [0, durationInFrames - 1], [0, 1]);
      g.drawChai(g.brush, chai, p, width, height, "#351D1D", "#F5EBDF");
      continueRender(h);
    });
  }, [frame, width, height, durationInFrames]);
  return <div ref={host} style={{ width, height, background: "#F5EBDF" }} />;
};

// Reel v5: the CTA scene's drawing (props scene.lineart), drawn on over `dur` seconds from `at`, then held: the step
// PNGs lineart-<id>-<k>.png are drawn by the slides' own renderer (reel.py lineart_frames), so no WebGL runs here.
export const ReelLineArt = (p: { id: string; frames: number; at: number; dur: number; t: number; top: number; size: number }) => {
  const k = Math.min(p.frames - 1, Math.max(0, Math.floor(((p.t - p.at) / p.dur) * p.frames)));
  return (
    <div style={{ position: "absolute", left: (1080 - p.size) / 2, top: p.top, width: p.size, height: p.size, display: "flex",
      alignItems: "center", justifyContent: "center" }}>
      <Img src={staticFile(`lineart-${p.id}-${k}.png`)} style={{ maxWidth: "100%", maxHeight: "100%" }} />
    </div>
  );
};
