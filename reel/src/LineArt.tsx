// Test composition: stroke-by-stroke draw-on of lineart/chai.json with p5.brush. Frame-deterministic: every frame
// clears and redraws from scratch (draw.js seeds the brush each call), so nothing carries between frames.
// brush.js, draw.js and chai.json come from the public dir (render-lineart.mjs copies them there).
import { useEffect, useRef } from "react";
import { continueRender, delayRender, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";

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

// Reel v5: the CTA scene's drawing (props scene.lineart: code/lineart data), drawn on stroke by stroke over `dur` seconds
// from `at` (by path length, like draw.js), then held: plain SVG strokes with a dash offset, no WebGL (the approved
// fallback; p5.brush under software GL dropped strokes at reel sizes).
type Pt = number[];
const plen = (p: Pt[]) => p.slice(1).reduce((s, q, i) => s + Math.hypot(q[0] - p[i][0], q[1] - p[i][1]), 0);
export const ReelLineArt = (p: { box: number[]; paths: Pt[][]; at: number; dur: number; t: number; top: number; size: number;
  ink: string }) => {
  const [x0, y0, x1, y1] = p.box, pad = 12, lens = p.paths.map(plen), total = lens.reduce((a, b) => a + b, 0);
  let left = Math.min(1, Math.max(0, (p.t - p.at) / p.dur)) * total;
  return (
    <svg style={{ position: "absolute", left: (1080 - p.size) / 2, top: p.top, width: p.size, height: p.size }}
      viewBox={`${x0 - pad} ${y0 - pad} ${x1 - x0 + 2 * pad} ${y1 - y0 + 2 * pad}`} preserveAspectRatio="xMidYMid meet">
      {p.paths.map((path, i) => {
        const shown = Math.min(lens[i], Math.max(0, left));
        left -= lens[i];
        return shown <= 0 ? null : (
          <path key={i} d={"M" + path.map((q) => q[0] + " " + q[1]).join(" L")} fill="none" stroke={p.ink}
            strokeWidth={(x1 - x0) / 90} strokeLinecap="round" strokeLinejoin="round"
            strokeDasharray={`${lens[i]} ${lens[i]}`} strokeDashoffset={lens[i] - shown} />
        );
      })}
    </svg>
  );
};
