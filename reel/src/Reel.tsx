import { useEffect, useMemo, useState } from "react";
import {
  AbsoluteFill, Audio, Easing, OffthreadVideo, Sequence, cancelRender, continueRender, delayRender, spring,
  staticFile, useCurrentFrame,
} from "remotion";
import { DIM, buildsUp, cardsShown, onScreen } from "./beats";
import { BrollBg, Captions, ChecklistView, PhoneEvidence, PullBack, zoomAt } from "./Fx";
import { SANS, SERIF, SERIF_ITALIC, SERIF_VARS, fit } from "./fit";
import L from "../layout.json";
import type { Card, Ground, Line, Palette, Presenter, ReelProps, Scene } from "./types";

const FPS = 30;
const WIPE_S = 0.35;
const BOX = 900;
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ease = Easing.out(Easing.cubic);

type Colors = { ink: string; sub: string; ground: string; emph: string; hi: string };
const colorsFor = (p: Palette, g: Ground): Colors => ({
  ink: g === "cream" ? p.ink : p.cream,
  sub: g === "cream" ? p.clay : p.blush,
  ground: p[g],
  emph: p.rust,
  hi: g === "cream" ? p.rust : p.cream,   // the word being spoken (on rust/maroon: full cream; earlier phrases are dimmed)
});

const fam = (family: string) => (family === SANS ? { fontFamily: family } : { fontFamily: family, fontVariationSettings: SERIF_VARS });

export const BrollLayer = (): null => null;

// D-026: the presenter clip fills the frame (muted: the voice is always our checked TTS track) behind a soft bottom
// gradient; its label is drawn on every frame the clip is on screen.
const PresenterLayer = (p: { pr: Presenter; sceneStart: number; c: Colors; scale: number }) => {
  const from = Math.round((p.pr.start - p.sceneStart) * FPS);
  return (
    <Sequence from={from} durationInFrames={Math.max(1, Math.round((p.pr.end - p.pr.start) * FPS))} layout="none">
      <AbsoluteFill style={{ transform: `scale(${p.scale})`, transformOrigin: "50% 30%" }}>
        <OffthreadVideo src={staticFile(p.pr.video)} muted style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: 1060,
        background: `linear-gradient(to bottom, transparent 0%, ${p.c.ground}d9 38%, ${p.c.ground} 70%)` }} />
      <div style={{
        position: "absolute", top: L.pill.top, left: 48, height: L.pill.height, boxSizing: "border-box", padding: "8px 22px", borderRadius: 999, fontFamily: SANS, fontWeight: 600,
        fontSize: 26, color: LABEL_INK, background: LABEL_BG, textShadow: "none",
      }}>{p.pr.label}</div>
    </Sequence>
  );
};
export const LABEL_BG = "#FFFFFF", LABEL_INK = "#111111";   // fixed so the QA can find the pill on decoded frames

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

const LH = 1.14;

// One font size for the WHOLE line, chosen before the first phrase appears, and the whole line laid out once: phrases
// not yet spoken take their place invisibly, so the build-up never reflows or shrinks. Too long a line fails the
// render (the writer keeps reel lines to 22 words).
const lineFit = (text: string, family: string, weight: number, height: number, max: number, min: number) => {
  for (let size = max; ;) {
    const r = fit(text, family, weight, 12, BOX - 20, size, 1);
    if (r.fontSize < min) throw new Error("line does not fit at " + min + "px: " + text);
    if (r.lines.length * r.fontSize * LH <= height) return r.fontSize;
    size = r.fontSize - 3;
  }
};

// A long health line as 2 cards (reel.py make_cards): the setting alone while it is spoken, then kept as a dimmed strip
// above the findings card, which is complete from its first word; each hero number pops when it is spoken.
const CardsView = (p: { line: Line; cards: Card[]; t: number; c: Colors; top: number; height: number; heroInk: string }) => {
  const { line, cards, t, c } = p;
  const setting = cards[0].parts[0].show;
  const big = useMemo(() => fit(setting, SANS, 800, 7, BOX - 20, 88, 48), [setting]);
  const strip = useMemo(() => fit(setting, SANS, 600, 4, BOX - 20, 42, 36), [setting]);
  const sizes = useMemo(() => cards[1].parts.map((q) => q.role === "hero" ? fit(q.show, SERIF, 700, 1, BOX, 210, 96).fontSize
    : fit(q.show, SANS, 700, 3, BOX - 20, 58, 40).fontSize), [cards]);
  const n = cardsShown(line, t);
  if (n === null) return null;
  const enter2 = ease(clamp01((t - cards[1].start) / 0.25));
  return (
    <>
      {n === 1 ? (
        <div style={{ position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: p.top, height: p.height, display: "flex",
          alignItems: "center", justifyContent: "center", textAlign: "center", fontFamily: SANS, fontWeight: 800,
          fontSize: big.fontSize, lineHeight: LH, color: c.ink, opacity: ease(clamp01((t - line.start) / 0.25)) }}>{setting}</div>
      ) : (
        <div style={{ position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: p.top, height: p.height, display: "flex",
          flexDirection: "column", alignItems: "center", textAlign: "center" }}>
          <div style={{ fontFamily: SANS, fontWeight: 600, fontSize: strip.fontSize, lineHeight: 1.25, color: c.ink, opacity: DIM,
            paddingBottom: 36 }}>{setting}</div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 6,
            opacity: enter2, transform: `translateY(${(1 - enter2) * 40}px)` }}>
            {cards[1].parts.map((q, i) => {
              if (q.role !== "hero") {
                return (<div key={i} style={{ fontFamily: SANS, fontWeight: 700, fontSize: sizes[i], lineHeight: 1.2, color: c.ink }}>{q.show}</div>);
              }
              const said = t >= q.at;
              const pop = said ? ease(clamp01((t - q.at) / 0.18)) : 0;
              return (
                <div key={i} style={{ fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 700, fontSize: sizes[i],
                  lineHeight: 1.05, whiteSpace: "nowrap", color: p.heroInk, opacity: 0.5 + 0.5 * pop,
                  transform: `scale(${0.92 + 0.08 * pop})` }}>{q.show}</div>
              );
            })}
          </div>
        </div>
      )}
      {line.source_label ? (
        <div style={{
          position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: L.source_label.top, height: L.source_label.height, textAlign: "center", fontFamily: SANS,
          fontWeight: 500, fontSize: 26, lineHeight: 1.25, color: c.sub,
        }}>{line.source_label}</div>
      ) : null}
    </>
  );
};

// A health line builds up phrase by phrase (earlier phrases stay, dimmed; nothing leaves before the hold ends); any
// other line is complete from its first frame with the spoken word highlighted (beats.ts).
const LineView = (p: { line: Line; t: number; c: Colors; top: number; height: number; appear: number; big: boolean }) => {
  const { line, t, c } = p;
  const flags = useMemo(() => emphasisFlags(line), [line]);
  const health = line.kind === "health";
  const st = health ? { family: SANS, weight: 800 } : { family: SERIF, weight: 600 };
  const font = useMemo(() => lineFit(line.text, st.family, st.weight, p.height, health ? 92 : p.big ? 120 : 92, health ? 48 : 44),
    [line, st.family, st.weight, p.height, health, p.big]);
  const s = onScreen(line, t, p.appear);
  if (!s) return null;
  return (
    <>
      <div style={{
        position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: p.top, height: p.height, display: "flex",
        alignItems: "center", justifyContent: "center",
      }}>
        <div style={{
          display: "flex", flexWrap: "wrap", justifyContent: "center", columnGap: font * 0.26, fontSize: font, lineHeight: LH,
          textAlign: "center", color: c.ink, ...fam(st.family), fontWeight: st.weight,
        }}>
          {line.beats.flatMap((b, k) => b.words.map((w, i) => {
            const shown = k < s.shown;
            const live = s.current === k;
            const active = live ? b.words.reduce((a, x, j) => (x.start <= t ? j : a), -1) : -1;
            const enter = buildsUp(line) ? ease(clamp01((t - b.start) / 0.22)) : 1;
            const opacity = !shown ? 0 : (buildsUp(line) && s.current !== null && k < s.current ? DIM : 1) * enter;   // the current phrase at full strength, earlier ones dimmed
            const on = live && i === active ? clamp01((t - w.start) / 0.1) : 0;
            return (
              <span key={k + "-" + i} style={{
                display: "inline-block", opacity, color: live && i === active ? c.hi : flags[k][i] ? c.emph : c.ink,
                transform: `translateY(${(1 - enter) * 30 - 6 * on}px)`,
                ...(flags[k][i] ? { fontFamily: SERIF_ITALIC, fontWeight: st.weight } : {}),
              }}>{w.text}</span>
            );
          }))}
        </div>
      </div>
      {line.source_label ? (
        <div style={{
          position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: L.source_label.top, height: L.source_label.height, textAlign: "center", fontFamily: SANS,
          fontWeight: 500, fontSize: 26, lineHeight: 1.25, color: c.sub,
        }}>{line.source_label}</div>
      ) : null}
    </>
  );
};

const KeyPair = (p: { number: string; label: string; at: number; t: number; frame: number; fromFrame: number; c: Colors; ground: Ground;
  top: number; height: number; numMax: number }) => {
  const num = useMemo(() => fit(p.number, SERIF, 700, 1, BOX, p.numMax, 56), [p.number, p.numMax]);
  const lab = useMemo(() => fit(p.label, SANS, 600, 3, BOX - 40, 44, 28), [p.label]);
  if (p.t < p.at) return null;
  const sp = spring({ frame: p.frame - Math.round(p.at * FPS) + p.fromFrame, fps: FPS, config: { damping: 9, stiffness: 150 } });
  return (
    <div style={{
      position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: p.top, height: p.height, display: "flex", flexDirection: "column",
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
    // every string on the card at least 40 px (1080 wide), so the DOI and location read on a phone
    let max = 44;
    for (;;) {
      const fits = s.entries.map((e) => fit(e.before + e.journal + e.after, SANS, 500, 16, BOX - 20, max, 1));
      const size = Math.min(...fits.map((f) => f.fontSize));
      const h = fits.reduce((a, f) => a + f.lines.length * size * 1.3 + 30, 0);
      if (size < 40) throw new Error("sources do not fit at 40px");
      if (h <= 1060) return size;
      max = size - 1;
    }
  }, [s]);
  const foot = useMemo(() => fit(s.footnote, SANS, 500, 6, BOX - 20, 40, 40), [s]);
  const url = useMemo(() => fit(s.url, SERIF, 600, 1, BOX, 56, 40), [s]);
  return (
    <div style={{
      position: "absolute", left: (1080 - BOX) / 2, width: BOX, top: L.sources.top, height: L.sources.height, display: "flex", flexDirection: "column",
      gap: 28, justifyContent: "center", color: c.ink,
    }}>
      <div style={{ fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 600, fontSize: 64 }}>{s.heading}</div>
      {s.entries.map((e, i) => (
        <div key={i} style={{ fontFamily: SANS, fontWeight: 500, fontSize: m, lineHeight: 1.3 }}>
          <span>{e.before}</span><span style={{ fontStyle: "italic", marginLeft: "0.3em" }}>{e.journal}</span><span>{e.after}</span>
        </div>
      ))}
      <div style={{ fontFamily: SANS, fontWeight: 500, fontSize: foot.fontSize, lineHeight: 1.35, color: c.sub }}>{s.footnote}</div>
      <div style={{ fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 600, fontSize: url.fontSize, color: c.emph, whiteSpace: "nowrap" }}>{s.url}</div>
    </div>
  );
};

const SceneView = (p: { scene: Scene; from: number; wipe: boolean; zoomIn: boolean; props: ReelProps; frozenAt?: number }) => {
  const { scene, props } = p;
  const frame = useCurrentFrame();
  const t = p.frozenAt ?? (frame + p.from) / FPS;
  const media = scene.presenter !== null || scene.broll !== null;
  const base = colorsFor(props.palette, scene.ground);
  // over a clip: white text with a soft shadow (inherited), blush accents
  const c = media ? { ...base, ink: "#FFFFFF", sub: props.palette.cream, emph: props.palette.blush, hi: props.palette.blush } : base;
  const keyLine = scene.lines.find((l) => l.key !== null);
  // with a presenter the text sits in the lower band, over the gradient; without, it uses the frame
  const box = scene.presenter ? (keyLine ? L.lines_presenter_key : L.lines_presenter) : (keyLine ? L.lines_key : L.lines);
  const keyBox = scene.presenter ? { ...L.keypair_presenter, numMax: 150 } : { ...L.keypair, numMax: 200 };
  const wp = p.wipe && p.frozenAt === undefined ? ease(clamp01(frame / (WIPE_S * FPS))) : 1;
  const zin = p.zoomIn && p.frozenAt === undefined ? clamp01(frame / (WIPE_S * FPS)) : 1;   // zoom-through cut: 1.25 -> 1
  const z = zoomAt(scene.zoom, t);
  const rule = ease(clamp01((t - scene.start) / 0.7));
  const dark = scene.ground !== "cream";
  return (
    <AbsoluteFill style={{ background: c.ground, clipPath: wp < 1 ? `inset(0 ${100 - wp * 100}% 0 0)` : undefined,
      textShadow: media ? "0 2px 14px rgba(0,0,0,0.6), 0 0 2px rgba(0,0,0,0.5)" : undefined,
      transform: zin < 1 ? `scale(${1.25 - 0.25 * ease(zin)})` : undefined, opacity: zin < 1 ? ease(zin) : undefined }}>
      {scene.broll ? <BrollBg b={scene.broll} sceneStart={scene.start} scale={z} origin="50% 40%" /> : null}
      {scene.presenter ? <PresenterLayer pr={scene.presenter} sceneStart={scene.start} c={c} scale={z} /> : null}
      {media ? null : (
        <div style={{
          position: "absolute", right: -140, bottom: 240, width: 420, height: 420, borderRadius: "50%",
          border: "3px solid " + (dark ? props.palette.blush : props.palette.rust), opacity: 0.35,
          transform: `translate(${Math.sin(t / 3) * 24}px, ${Math.cos(t / 4) * 30}px)`,
        }} />
      )}
      <div style={{ position: "absolute", left: 60, top: L.wordmark.top - 14, height: 4, width: 960 * rule, background: props.palette.blush }} />
      {scene.lines.map((l, i) => l.cards ? (
        <div key={i}>
          {media ? (   // glass card: a frosted panel behind the two cards
            <div style={{ position: "absolute", left: 50, width: 980, top: box.top - 40, height: box.height + 80, borderRadius: 40,
              background: "rgba(245, 235, 223, 0.16)", backdropFilter: "blur(18px)", border: "1px solid rgba(245, 235, 223, 0.35)" }} />
          ) : null}
          <CardsView line={l} cards={l.cards} t={t} c={c} top={box.top} height={box.height}
            heroInk={scene.ground === "cream" && !media ? c.emph : c.ink} />
          {l.evidence ? <PhoneEvidence e={l.evidence} t={t} ink={props.palette.ink} accent={props.palette.rust} /> : null}
        </div>
      ) : scene.captions ? (
        <Captions key={i} line={l} t={t} accent={props.palette.blush} emph={emphasisFlags(l)} />
      ) : (
        <LineView key={i} line={l} t={t} c={c} top={box.top} height={box.height} appear={i === 0 ? scene.start : l.start}
          big={scene.kind === "hook" && i === 0} />
      ))}
      {keyLine && keyLine.key ? (
        <KeyPair number={keyLine.key.number} label={keyLine.key.label} at={keyLine.key.at} t={t} frame={frame}
          fromFrame={p.from} c={c} ground={scene.ground} {...keyBox} />
      ) : null}
      {scene.checklist ? <ChecklistView c={scene.checklist} t={t} ink={c.ink} accent={props.palette.rust} /> : null}
      {scene.sources ? <SourcesCard s={scene.sources} c={c} /> : null}
      <div style={{
        position: "absolute", top: L.pill.top, right: 48, height: L.pill.height, boxSizing: "border-box", padding: "8px 22px", borderRadius: 999, fontFamily: SANS, fontWeight: 600,
        fontSize: 26, color: media ? "#FFFFFF" : c.sub, border: "2px solid " + (media ? "#FFFFFF" : c.sub),
        background: media ? "rgba(0,0,0,0.25)" : undefined,
      }}>{props.chrome.draft_mark}</div>
      <div style={{
        position: "absolute", left: 60, top: L.wordmark.top, height: L.wordmark.height, lineHeight: 1, fontFamily: SERIF, fontVariationSettings: SERIF_VARS, fontWeight: 600,
        fontSize: 44, color: media ? "#FFFFFF" : c.ink,
      }}>{props.chrome.wordmark}</div>
    </AbsoluteFill>
  );
};

const PullBackScene = (p: { s: Scene; prev: Scene; from: number; props: ReelProps }) => {
  const frame = useCurrentFrame();
  const t = (frame + p.from) / FPS;
  return (
    <PullBack image={p.s.pullback as string} t={t} start={p.s.start} end={p.s.end}>
      <SceneView scene={p.prev} from={Math.round(p.prev.start * FPS)} wipe={false} zoomIn={false} props={p.props}
        frozenAt={p.prev.end - 0.04} />
    </PullBack>
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
        const tail = next && next.transition_in !== "none" ? Math.round(WIPE_S * FPS) : 0;
        const dur = Math.min(total - from, Math.round(s.end * FPS) - from + tail);
        const prev = props.scenes[i - 1];
        return (
          <Sequence key={i} from={from} durationInFrames={Math.max(1, dur)}>
            {s.kind === "pullback" && s.pullback && prev ? (
              // the previous scene's last frame shrinks into the real studio screen
              <PullBackScene s={s} prev={prev} from={from} props={props} />
            ) : (
              <SceneView scene={s} from={from} wipe={s.transition_in === "wipe"} zoomIn={s.transition_in === "zoom"} props={props} />
            )}
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
