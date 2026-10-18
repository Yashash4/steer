// Draws lineart/chai.json with p5.brush (standalone build, global `brush`). Stateless: every call clears and
// redraws from scratch with the same seed, so a frame depends only on `progress` (0..1, by total path length).
// Used by both the test slide (trials/lineart-1/slide.html) and the Remotion composition (src/LineArt.tsx).
// "ink": even-weight marker nib, no grain, tiny scatter = slight hand wobble. WEIGHT is canvas units (x scaleBrushes).
window.INK_WEIGHT = 7;
window.drawChai = function (brush, chai, progress, W, H, ink, bg) {
  const len = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
  const plen = (p) => p.slice(1).reduce((s, q, i) => s + len(p[i], q), 0);
  const total = chai.paths.reduce((s, p) => s + plen(p), 0);
  let left = progress * total;
  if (!brush.box().includes("ink"))
    brush.add("ink", { type: "marker", weight: 1, scatter: 0.12, sharpness: 1, grain: 0, opacity: 255, spacing: 0.15,
      pressure: [1, 1], noise: 0, markerTip: false });
  brush.seed(7);
  brush.noiseSeed(7);
  brush.clear(bg);
  brush.push();
  const s = Math.min(W, H) / chai.box;
  brush.translate(-W / 2 + (W - chai.box * s) / 2, -H / 2 + (H - chai.box * s) / 2);
  brush.scale(s);
  brush.set("ink", ink, window.INK_WEIGHT);
  for (const p of chai.paths) {
    if (left <= 0) break;
    const L = plen(p);
    let pts = p;
    if (left < L) {  // cut the polyline at `left` along its length
      pts = [p[0]];
      let r = left;
      for (let i = 1; i < p.length; i++) {
        const d = len(p[i - 1], p[i]);
        if (r >= d) { pts.push(p[i]); r -= d; continue; }
        const t = r / d;
        pts.push([p[i - 1][0] + (p[i][0] - p[i - 1][0]) * t, p[i - 1][1] + (p[i][1] - p[i - 1][1]) * t]);
        break;
      }
    }
    left -= L;
    if (pts.length === 2 && plen(pts) > 2) brush.line(pts[0][0], pts[0][1], pts[1][0], pts[1][1]);
    else if (pts.length > 2 && plen(pts) > 2) brush.spline(pts, 0.5);
  }
  brush.pop();
  brush.render();
};
