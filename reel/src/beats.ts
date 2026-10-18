// Which beats of a line are on screen at time t. Pure, so test/beats.mjs can check every frame of a line.
//
// Meaning safety: a still frame must never read the opposite of the claim. On a health line the OPENING beat stays
// pinned (dimmed, at the top) until the line's hold, and so does every beat holding a negation or qualifier word
// ("No evidence was found that ..." must not scroll away before "... changed the rate of ...").
// No overlap: the beat leaving is gone (EXIT_S) before the next one starts to appear (ENTER_DELAY).
import type { Line } from "./types";

export const EXIT_S = 0.12;
export const ENTER_DELAY = 0.12;
const QUALIFIER = /\b(no|not|never|without|only|may|might|could|associated|among)\b|\bin this study\b/i;

export type OnScreen = { hold: boolean; pinned: number[]; current: number | null; exiting: number | null };

export const pinnedSet = (line: Line): Set<number> =>
  new Set(line.kind !== "health" ? [] : line.beats.flatMap((b, k) => (k === 0 || QUALIFIER.test(b.text) ? [k] : [])));

export const onScreen = (line: Line, t: number): OnScreen | null => {
  if (t < line.start || t >= line.hold_end) return null;
  if (t >= line.end) return { hold: true, pinned: [], current: null, exiting: null };
  let i = 0;
  line.beats.forEach((b, k) => { if (b.start <= t) i = k; });
  const pins = pinnedSet(line);
  const pinned = [...pins].filter((k) => k < i).sort((a, b) => a - b);
  const prev = i - 1;
  const exiting = prev >= 0 && !pins.has(prev) && t - line.beats[i].start < EXIT_S ? prev : null;
  return { hold: false, pinned, current: i, exiting };
};

/** The strings the line puts on screen at t (the hold draws the whole checked line). */
export const visibleTexts = (line: Line, t: number): string[] => {
  const s = onScreen(line, t);
  if (!s) return [];
  if (s.hold) return [line.text];
  return [...s.pinned, ...(s.current !== null ? [s.current] : []), ...(s.exiting !== null ? [s.exiting] : [])]
    .map((k) => line.beats[k].text);
};
