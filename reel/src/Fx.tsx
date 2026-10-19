// Reel v4 effects (docs/topics/reel-v4-edit-plan.md). Every string drawn here comes from props, which reel.py's pool
// check has already matched to checked script lines, code-built status lines or source metadata.
import { AbsoluteFill, Img, OffthreadVideo, Sequence, staticFile } from "remotion";
import L from "../layout.json";
import { SANS, SERIF, SERIF_VARS, fit } from "./fit";
import type { Broll, Card, Checklist, Evidence, Line, Zoom } from "./types";

const FPS = 30;
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const easeOut = (x: number) => 1 - Math.pow(1 - clamp01(x), 3);

/** Digital zoom: a punch-in on the background layer (never on text), 1.00 -> 1.10 and back, centred on the eyes. */
export const zoomAt = (zooms: Zoom[], t: number): number => {
  let s = 1;
  for (const z of zooms) {
    const u = (t - z.at) / z.dur;
    if (u >= 0 && u <= 1) s = Math.max(s, 1 + (z.scale - 1) * Math.sin(Math.PI * u));
  }
  return s;
};

/** A muted b-roll clip filling the frame behind the scene, darkened a little so the text stays legible. */
export const BrollBg = (p: { b: Broll; sceneStart: number; scale: number; origin: string }) => (
  <Sequence from={Math.round((p.b.start - p.sceneStart) * FPS)} durationInFrames={Math.max(1, Math.round((p.b.end - p.b.start) * FPS))}
    layout="none">
    <AbsoluteFill style={{ transform: `scale(${p.scale})`, transformOrigin: p.origin }}>
      <OffthreadVideo src={staticFile(p.b.video)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
    </AbsoluteFill>
    <AbsoluteFill style={{ background: "rgba(20, 10, 10, 0.28)" }} />
  </Sequence>
);

/** Chunked captions: the beat being spoken (2-4 words, a whole run of the line), Inter bold white with a soft shadow;
 * emphasis words and number tokens in the accent colour, 1.3x. */
export const Captions = (p: { line: Line; t: number; accent: string; emph: boolean[][] }) => {
  const { line, t } = p;
  if (t < line.start || t >= line.end) return null;
  let k = 0;
  line.beats.forEach((b, i) => { if (b.start <= t) k = i; });
  const beat = line.beats[k];
  const size = fit(beat.text, SANS, 800, 2, 900, 76, 48).fontSize;
  return (
    <div style={{ position: "absolute", left: 90, width: 900, top: L.captions.top, height: L.captions.height, display: "flex",
      alignItems: "center", justifyContent: "center" }}>
      <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", alignItems: "baseline", columnGap: size * 0.26,
        fontFamily: SANS, fontWeight: 800, fontSize: size, lineHeight: 1.12, color: "#FFFFFF", textAlign: "center",
        textShadow: "0 2px 12px rgba(0,0,0,0.55), 0 0 2px rgba(0,0,0,0.6)" }}>
        {beat.words.map((w, i) => {
          const key = p.emph[k][i] || /\d/.test(w.text);
          return (<span key={i} style={{ color: key ? p.accent : "#FFFFFF", fontSize: key ? size * 1.3 : size }}>{w.text}</span>);
        })}
      </div>
    </div>
  );
};

/** The evidence beat: a phone frame with the claim's real page (CC BY papers) or, otherwise, the exact quote typeset by
 * code under a fixed header; the quoted sentence highlighted. */
export const PhoneEvidence = (p: { e: Evidence; t: number; ink: string; accent: string }) => {
  const { e, t } = p;
  if (t < e.start || t >= e.end) return null;
  const k = easeOut((t - e.start) / 0.35);
  const PW = 640, PH = L.evidence.height, top = L.evidence.top;   // between the setting line and the captions
  // the boxes as drawn: screen = PW - 2 x 18; header padding 40 each side; quote margin 40 + padding 24 each side + 6 border
  const h = fit(e.header, SANS, 600, 3, PW - 36 - 80, L.evidence.px, L.evidence.px);
  let q: ReturnType<typeof fit> | null = null;
  let shown = e.quote, cut = false;
  const base = PH - 36 - 54 - 18 - h.lines.length * h.fontSize * 1.3 - 20 - 40 - 24;
  // the largest size >= the floor (34 px: the reviewer could not read it) whose lines fit the screen's height
  const tryFit = (text: string, room: number) => {
    for (let size = L.evidence.px + 12; size >= L.evidence.px; size--) {
      const lines = Math.floor(room / (size * 1.35));
      try { if (lines > 0) return fit(text, SERIF, 500, lines, PW - 36 - 80 - 48 - 6, size, size); } catch { /* next size */ }
    }
    return null;
  };
  if (!e.image) {
    q = tryFit(e.quote, base);
    if (!q && e.truncate_ok && e.quote_short) {   // guard (computed in Python): whole sentences that keep the line's numbers and population words
      shown = (e.cut_start ? "\u2026 " : "") + e.quote_short + (e.cut_end ? " \u2026" : "");
      q = tryFit(shown, base - L.evidence.px * 1.3 - 14);   // room for the "full sentence" line under it
      cut = q !== null;
    }
    if (!q) throw new Error("evidence quote does not fit the phone at " + L.evidence.px + "px");
  }
  return (
    <div style={{ position: "absolute", left: (1080 - PW) / 2, top, width: PW, height: PH, borderRadius: 64, background: "#1B1414",
      padding: 18, boxSizing: "border-box", boxShadow: "0 30px 80px rgba(0,0,0,0.35)", opacity: k,
      transform: `translateY(${(1 - k) * 60}px) scale(${0.94 + 0.06 * k})` }}>
      <div style={{ width: "100%", height: "100%", borderRadius: 48, overflow: "hidden", background: "#F8F2EB", position: "relative" }}>
        <div style={{ padding: "54px 40px 18px", fontFamily: SANS, fontWeight: 600, fontSize: h.fontSize, lineHeight: 1.3,
          color: "#7A5A50" }}>{e.header}</div>
        {e.image ? (
          <div style={{ position: "absolute", left: 0, right: 0, top: 160, bottom: 0, overflow: "hidden" }}>
            <div style={e.width ? { position: "absolute", top: 40, left: 0, width: e.width, height: e.height,
              // the readable strip of the quoted lines (text ~30 px), panned left to right across the evidence window
              transform: `translateX(${-Math.max(0, e.width - (PW - 36) + 40) * clamp01((t - e.start - 0.6) / Math.max(1, e.end - e.start - 1.6))
                - 0}px)` } : { position: "relative" }}>   {/* the boxes are fractions of the image, so they sit on it */}
              <Img src={staticFile(e.image)} style={e.width ? { width: e.width, height: e.height, display: "block" } : { width: "100%", display: "block" }} />
              {e.boxes.map((b, i) => (
                <div key={i} style={{ position: "absolute", left: `${b[0] * 100}%`, top: `${b[1] * 100}%`, width: `${(b[2] - b[0]) * 100}%`,
                  height: `${(b[3] - b[1]) * 100}%`, background: "rgba(192, 78, 42, 0.28)", borderBottom: `3px solid ${p.accent}` }} />
              ))}
            </div>
          </div>
        ) : (
          <div style={{ margin: "10px 40px", padding: "20px 24px", borderLeft: `6px solid ${p.accent}`, background: "rgba(192, 78, 42, 0.10)",
            fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 500, fontSize: q?.fontSize, lineHeight: 1.35,
            color: p.ink }}>{shown}{cut ? (
              <div style={{ marginTop: 14, fontFamily: SANS, fontWeight: 600, fontSize: L.evidence.px, lineHeight: 1.3, color: "#7A5A50" }}>the full sentence: {e.full_label}</div>
            ) : null}</div>
        )}
      </div>
    </div>
  );
};

/** The checklist: fixed status lines, each in props only because the trace proves it; ticked one by one. */
export const ChecklistView = (p: { c: Checklist; t: number; ink: string; accent: string; low?: boolean }) => (
  <div style={{ position: "absolute", left: 120, width: 840, top: p.low ? L.checklist_presenter.top : L.checklist.top,
    height: p.low ? L.checklist_presenter.height : L.checklist.height, display: "flex",
    flexDirection: "column", justifyContent: "center", gap: 46 }}>
    {p.c.items.map((it, i) => {
      const k = easeOut((p.t - it.at) / 0.3);
      return (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 30, opacity: 0.25 + 0.75 * k }}>
          <div style={{ width: 64, height: 64, borderRadius: 32, border: `4px solid ${p.accent}`,
            background: it.done && k > 0.5 ? p.accent : "transparent",   // a pending item is never ticked
            flex: "none", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ width: 26, height: 14, borderLeft: "5px solid #F5EBDF", borderBottom: "5px solid #F5EBDF",
              transform: `rotate(-45deg) scale(${it.done ? k : 0})`, marginTop: -6 }} />
          </div>
          <div style={{ fontFamily: SANS, fontWeight: 700, fontSize: p.low ? 38 : 46, lineHeight: 1.2, color: p.ink }}>{it.text}</div>
        </div>
      );
    })}
  </div>
);

/** Pull-back: the reel frame shrinks into the real studio screen (a screenshot taken at render time). */
export const PullBack = (p: { image: string; t: number; start: number; end: number; children: React.ReactNode }) => {
  const u = easeOut((p.t - p.start) / Math.max(0.5, (p.end - p.start) * 0.7));
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ opacity: u }}>
        <Img src={staticFile(p.image)} style={{ width: "100%", height: "100%", objectFit: "cover", filter: "brightness(0.92)" }} />
      </AbsoluteFill>
      <AbsoluteFill style={{ transform: `translate(${u * 150}px, ${u * 120}px) scale(${1 - 0.62 * u})`, transformOrigin: "50% 40%",
        borderRadius: 40 * u, overflow: "hidden", boxShadow: u > 0 ? "0 30px 90px rgba(0,0,0,0.45)" : "none" }}>
        {p.children}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** v5: the numbers line as stacked glass cards over the presenter; each card appears when its number is spoken and
 * stays to the line's hold end; hero parts large, text parts small (the population is card 1's small text). */
export const StackCards = (p: { line: Line; cards: Card[]; t: number; accent: string }) => {
  const { line, cards, t } = p;
  if (t < line.start || t >= line.hold_end) return null;
  return (
    <div style={{ position: "absolute", left: 60, width: 960, top: L.hero_cards.top, height: L.hero_cards.height, display: "flex",
      flexDirection: "column", justifyContent: "flex-end", gap: 22 }}>
      {cards.map((c, i) => {
        if (t < c.start) return null;
        const k = easeOut((t - c.start) / 0.25);
        return (
          <div key={i} style={{ borderRadius: 32, padding: "22px 30px", background: "rgba(245, 235, 223, 0.18)",
            backdropFilter: "blur(16px)", border: "1px solid rgba(245, 235, 223, 0.4)", opacity: k,
            transform: `translateY(${(1 - k) * 40}px) scale(${0.94 + 0.06 * k})`, display: "flex", flexWrap: "wrap",
            alignItems: "baseline", columnGap: 14, color: "#FFFFFF", textShadow: "0 2px 10px rgba(0,0,0,0.5)" }}>
            {c.parts.map((q, j) => q.role === "hero" ? (
              <span key={j} style={{ fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 700,
                fontSize: fit(q.show, SERIF, 700, 1, 900, 120, 72).fontSize, lineHeight: 1.0, color: p.accent }}>{q.show}</span>
            ) : (
              <span key={j} style={{ fontFamily: SANS, fontWeight: 600, fontSize: fit(q.show, SANS, 600, 4, 900, 38, 30).fontSize,
                lineHeight: 1.25 }}>{q.show}</span>
            ))}
          </div>
        );
      })}
    </div>
  );
};
