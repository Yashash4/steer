import { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill, Audio, Easing, Sequence, cancelRender, continueRender, delayRender, spring,
  staticFile, useCurrentFrame,
} from "remotion";
import { ENTER_DELAY, EXIT_S, onScreen, pinnedSet } from "./beats";
import { SANS, SERIF, SERIF_ITALIC, SERIF_VARS, fit } from "./fit";
import type { Beat, Ground, Line, Palette, ReelProps, Scene } from "./types";

const FPS = 30;
const WIPE_S = 0.35;
const BOX = 900;
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ease = Easing.out(Easing.cubic);

type Colors = { ink: string; sub: string; ground: string; emph: string };
const colorsFor = (p: Palette, g: Ground): Colors => ({
  ink: g === "cream" ? p.ink : p.cream,
  sub: g === "cream" ? p.clay : p.blush,
  ground: p[g],
  emph: p.rust,
});

// Fraunces for framing/brand/cta, Inter 800 for health beats.
const beatStyle = (line: Line) =>
  line.kind === "health" ? { family: SANS, weight: 800 } : { family: SERIF, weight: 600 };
const holdStyle = (line: Line) =>
  line.kind === "health" ? { family: SANS, weight: 700 } : { family: SERIF, weight: 500 };
const fam = (family: string) => (family === SANS ? { fontFamily: family } : { fontFamily: family, fontVariationSettings: SERIF_VARS });

export const BrollLayer = (): null => null;

// Which words of the line sit inside line.emphasis (matched by exact substring).
const emphasisFlags = (line: Line): boolean[][] => {
  const at = line.emphasis ? line.text.indexOf(line.emphasis) : -1;
  const len = line.emphasis ? line.emphasis.length : 0;
  let cursor = 0;
  return line.beats.map((b) =>
    b.words.map((w) => {
      const pos = line.text.indexOf(w.text, cursor);
      if (pos < 0) return false;
      cursor = pos + w.text.length;
      return at >= 0 && pos >= at && pos < at + len;
    }),
  );
};

const BeatView = (p: { line: Line; beat: Beat; flags: boolean[]; t: number; exitAge: number | null; c: Colors; top: number; height: number }) => {
  const { line, beat, flags, t, exitAge, c } = p;
  const st = beatStyle(line);
  const fitted = useMemo(() => fit(beat.text, st.family, st.weight, 2, BOX - 20, 150, 56), [beat, st.family, st.weight]);
  // the next beat starts to appear only after the leaving one is gone: never two beats on screen
  const enter = ease(clamp01((t - beat.start - ENTER_DELAY) / 0.22));
  let y = (1 - enter) * 60;
  let opacity = enter;
  if (exitAge !== null) {
    y = -120 * ease(clamp01(exitAge / EXIT_S));
    opacity = 1 - clamp01(exitAge / EXIT_S);
  }
  const activeIdx = exitAge !== null ? -1 : beat.words.reduce((a, w, i) => (w.start <= t ? i : a), 0);
  return (
    <div style={{
      position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: p.top, height: p.height, display: "flex",
      alignItems: "center", justifyContent: "center", transform: `translateY(${y}px)`, opacity,
    }}>
      <div style={{
        display: "flex", flexWrap: "wrap", justifyContent: "center", columnGap: fitted.fontSize * 0.28,
        fontSize: fitted.fontSize, lineHeight: 1.1, color: c.ink, textAlign: "center", ...fam(st.family), fontWeight: st.weight,
      }}>
        {beat.words.map((w, i) => {
          const next = beat.words[i + 1];
          const on = exitAge !== null ? 0 : clamp01((t - w.start) / 0.1) * (next ? 1 - clamp01((t - next.start) / 0.1) : 1);
          const said = exitAge !== null || i <= activeIdx;
          const em = flags[i];
          return (
            <span key={i} style={{
              display: "inline-block", opacity: said ? 1 : 0.4, color: em ? c.emph : c.ink,
              transform: `translateY(${-10 * on}px) scale(${1 + 0.06 * on})`,
              ...(em ? { fontFamily: SERIF_ITALIC, fontWeight: st.weight } : {}),
            }}>{w.text}</span>
          );
        })}
      </div>
    </div>
  );
};

const HoldView = (p: { line: Line; c: Colors; top: number; height: number; t: number }) => {
  const { line, c } = p;
  const st = holdStyle(line);
  const fitted = useMemo(() => {
    let max = 84;
    for (;;) {
      const r = fit(line.text, st.family, st.weight, 8, BOX - 40, max, 28);
      if (r.lines.length * r.fontSize * 1.2 <= p.height) return r;
      max = r.fontSize - 2;
      if (max < 28) throw new Error("full line does not fit at 28px: " + line.text);
    }
  }, [line, st.family, st.weight, p.height]);
  const at = line.emphasis ? line.text.indexOf(line.emphasis) : -1;
  const emLen = line.emphasis ? line.emphasis.length : 0;
  const parts = at < 0 ? [line.text, "", ""] : [line.text.slice(0, at), line.text.slice(at, at + emLen), line.text.slice(at + emLen)];
  return (
    <div style={{
      position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: p.top, height: p.height, display: "flex",
      alignItems: "center", justifyContent: "center", opacity: clamp01((p.t - line.end) / 0.15),
    }}>
      <div style={{
        fontSize: fitted.fontSize, lineHeight: 1.2, textAlign: "center", color: c.ink, ...fam(st.family), fontWeight: st.weight,
      }}>
        <span>{parts[0]}</span>
        <span style={{ color: c.emph, fontFamily: SERIF_ITALIC, fontStyle: "normal" }}>{parts[1]}</span>
        <span>{parts[2]}</span>
      </div>
    </div>
  );
};

// The opening beat of a health line, and every beat with a negation or qualifier, stay pinned at the top (dimmed)
// until the hold: a still frame never loses "No evidence was found that ..." (beats.ts).
const PIN_MAX = 64, PIN_MIN = 32, PIN_GAP = 12;

const LineView = (p: { line: Line; t: number; c: Colors; hasKey: boolean; keyAt: number | null }) => {
  const { line, t, c } = p;
  const flags = useMemo(() => emphasisFlags(line), [line]);
  const st = beatStyle(line);
  const pinFits = useMemo(() => {
    const pins = pinnedSet(line);
    return line.beats.map((b, k) => (pins.has(k) ? fit(b.text, st.family, st.weight, 2, BOX, PIN_MAX, PIN_MIN) : null));
  }, [line, st.family, st.weight]);
  const s = onScreen(line, t, p.keyAt !== null && t >= p.keyAt);   // a key pair on screen counts as a pinned block
  if (!s) return null;
  const top = p.hasKey ? 700 : 240;
  const pinH = s.pinned.reduce((a, k) => a + (pinFits[k]?.lines.length ?? 1) * (pinFits[k]?.fontSize ?? PIN_MAX) * 1.12 + PIN_GAP, 0);
  const beatTop = top + pinH + (s.pinned.length ? 24 : 0);
  const height = 1640 - beatTop;
  const age = s.current !== null ? t - line.beats[s.current].start : 0;
  return (
    <>
      {s.pinned.length ? (
        <div style={{ position: "absolute", left: (1080 - BOX) / 2, width: BOX, top, display: "flex", flexDirection: "column",
          gap: PIN_GAP, alignItems: "center", opacity: 0.55 }}>
          {s.pinned.map((k) => (
            <div key={k} style={{ fontSize: pinFits[k]?.fontSize ?? PIN_MAX, lineHeight: 1.12, textAlign: "center", color: c.ink,
              ...fam(st.family), fontWeight: st.weight }}>{line.beats[k].text}</div>
          ))}
        </div>
      ) : null}
      {s.exiting !== null ? (
        <BeatView key={s.exiting} line={line} beat={line.beats[s.exiting]} flags={flags[s.exiting]} t={t} exitAge={age} c={c}
          top={beatTop} height={height} />
      ) : null}
      {s.current !== null ? (
        <BeatView key={s.current} line={line} beat={line.beats[s.current]} flags={flags[s.current]} t={t} exitAge={null} c={c}
          top={beatTop} height={height} />
      ) : null}
      {s.hold ? <HoldView line={line} c={c} top={top} height={1640 - top} t={t} /> : null}
      {line.source_label ? (
        <div style={{
          position: "absolute", left: (1080 - BOX) / 2, width: BOX, bottom: 200, textAlign: "center", fontFamily: SANS,
          fontWeight: 500, fontSize: 26, lineHeight: 1.25, color: c.sub,
        }}>{line.source_label}</div>
      ) : null}
    </>
  );
};

const KeyPair = (p: { number: string; label: string; at: number; t: number; frame: number; fromFrame: number; c: Colors; ground: Ground }) => {
  const num = useMemo(() => fit(p.number, SERIF, 700, 1, BOX, 200, 56), [p.number]);
  const lab = useMemo(() => fit(p.label, SANS, 600, 3, BOX - 40, 44, 28), [p.label]);
  if (p.t < p.at) return null;
  const sp = spring({ frame: p.frame - Math.round(p.at * FPS) + p.fromFrame, fps: FPS, config: { damping: 9, stiffness: 150 } });
  return (
    <div style={{
      position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: 170, height: 500, display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 24, transform: `scale(${0.85 + 0.15 * sp})`,
      opacity: clamp01((p.t - p.at) / 0.12),
    }}>
      <div style={{
        fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 700, fontSize: num.fontSize, lineHeight: 1.05,
        whiteSpace: "nowrap", color: p.ground === "cream" ? p.c.emph : p.c.ink,
      }}>{p.number}</div>
      <div style={{
        fontFamily: SANS, fontWeight: 600, fontSize: lab.fontSize, lineHeight: 1.2, textAlign: "center", color: p.c.ink,
      }}>{p.label}</div>
    </div>
  );
};

const SourcesCard = (p: { s: NonNullable<Scene["sources"]>; c: Colors }) => {
  const { s, c } = p;
  const m = useMemo(() => {
    let max = 30;
    for (;;) {
      const fits = s.entries.map((e) => fit(e.before + e.journal + e.after, SANS, 500, 14, BOX - 20, max, 24));
      const size = Math.min(...fits.map((f) => f.fontSize));
      const h = fits.reduce((a, f) => a + f.lines.length * size * 1.4 + 36, 0);
      if (h <= 900) return size;
      max = size - 1;
      if (max < 24) throw new Error("sources do not fit at 24px");
    }
  }, [s]);
  const foot = useMemo(() => fit(s.footnote, SANS, 500, 5, BOX - 20, 28, 24), [s]);
  const url = useMemo(() => fit(s.url, SERIF, 600, 1, BOX, 56, 40), [s]);
  return (
    <div style={{
      position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: 230, bottom: 260, display: "flex", flexDirection: "column",
      gap: 36, justifyContent: "center", color: c.ink,
    }}>
      <div style={{ fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 600, fontSize: 64 }}>{s.heading}</div>
      {s.entries.map((e, i) => (
        <div key={i} style={{ fontFamily: SANS, fontWeight: 500, fontSize: m, lineHeight: 1.4 }}>
          <span>{e.before}</span><span style={{ fontStyle: "italic", marginLeft: "0.3em" }}>{e.journal}</span><span>{e.after}</span>
        </div>
      ))}
      <div style={{ fontFamily: SANS, fontWeight: 500, fontSize: foot.fontSize, lineHeight: 1.35, color: c.sub }}>{s.footnote}</div>
      <div style={{ fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 600, fontSize: url.fontSize, color: c.emph, whiteSpace: "nowrap" }}>{s.url}</div>
    </div>
  );
};

const SceneView = (p: { scene: Scene; from: number; wipe: boolean; props: ReelProps }) => {
  const { scene, props } = p;
  const frame = useCurrentFrame();
  const t = (frame + p.from) / FPS;
  const c = colorsFor(props.palette, scene.ground);
  const hasKey = scene.lines.some((l) => l.key !== null);
  const keyLine = scene.lines.find((l) => l.key !== null);
  const wp = p.wipe ? ease(clamp01(frame / (WIPE_S * FPS))) : 1;
  const sinceStart = t - scene.start;
  const rule = ease(clamp01(sinceStart / 0.7));
  const dark = scene.ground !== "cream";
  return (
    <AbsoluteFill style={{ background: c.ground, clipPath: wp < 1 ? `inset(0 ${100 - wp * 100}% 0 0)` : undefined }}>
      {scene.kind !== "point" && scene.kind !== "sources" ? <BrollLayer /> : null}
      <div style={{
        position: "absolute", right: -140, bottom: 240, width: 420, height: 420, borderRadius: "50%",
        border: "3px solid " + (dark ? props.palette.blush : props.palette.rust), opacity: 0.35,
        transform: `translate(${Math.sin(t / 3) * 24}px, ${Math.cos(t / 4) * 30}px)`,
      }} />
      <div style={{ position: "absolute", left: 60, bottom: 150, height: 4, width: 960 * rule, background: props.palette.blush }} />
      {scene.lines.map((l, i) => <LineView key={i} line={l} t={t} c={c} hasKey={hasKey} keyAt={keyLine?.key?.at ?? null} />)}
      {keyLine && keyLine.key ? (
        <KeyPair number={keyLine.key.number} label={keyLine.key.label} at={keyLine.key.at} t={t} frame={frame}
          fromFrame={p.from} c={c} ground={scene.ground} />
      ) : null}
      {scene.sources ? <SourcesCard s={scene.sources} c={c} /> : null}
      <div style={{
        position: "absolute", top: 64, right: 48, padding: "8px 22px", borderRadius: 999, fontFamily: SANS, fontWeight: 600,
        fontSize: 26, color: c.sub, border: "2px solid " + c.sub,
      }}>{props.chrome.draft_mark}</div>
      <div style={{
        position: "absolute", left: 60, bottom: 64, fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 600,
        fontSize: 44, color: c.ink,
      }}>{props.chrome.wordmark}</div>
    </AbsoluteFill>
  );
};

export const Reel = (props: ReelProps) => {
  const [ready, setReady] = useState(false);
  const [handle] = useState(() => delayRender("fonts"));
  useEffect(() => {
    const load = (family: string, file: string, style: string) =>
      new FontFace(family, "url(" + staticFile(file) + ")", { weight: "100 900", style }).load();
    Promise.all([
      load(SERIF, props.fonts.serif, "normal"), load(SERIF_ITALIC, props.fonts.serif_italic, "normal"),
      load(SANS, props.fonts.sans, "normal"),
    ]).then((faces) => {
      faces.forEach((f) => (document.fonts as unknown as { add(f: FontFace): void }).add(f));
      setReady(true);
      continueRender(handle);
    }).catch((e) => cancelRender(e));
  }, [handle, props.fonts]);
  if (!ready) return null;
  const total = props.durationInFrames;
  return (
    <AbsoluteFill style={{ background: props.palette.cream }}>
      <Audio src={staticFile(props.audio)} />
      {props.scenes.map((s, i) => {
        const from = Math.round(s.start * FPS);
        const next = props.scenes[i + 1];
        const tail = next && next.transition_in === "wipe" ? Math.round(WIPE_S * FPS) : 0;
        const dur = Math.min(total - from, Math.round(s.end * FPS) - from + tail);
        return (
          <Sequence key={i} from={from} durationInFrames={Math.max(1, dur)}>
            <SceneView scene={s} from={from} wipe={s.transition_in === "wipe"} props={props} />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
