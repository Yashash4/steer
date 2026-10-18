// Which beats of a line are on screen at time t. Pure, so test/beats.mjs can check every frame of a line.
//
// Meaning safety: a still frame must never read the opposite of the claim. On a health line the OPENING beat stays
// pinned (dimmed, at the top) until the line's hold, and so does every beat holding a negation or qualifier word
// ("No evidence was found that ..." must not scroll away before "... changed the rate of ...").
// At most 2 pinned blocks: the opening beat + the most recent qualifier beat (an older qualifier beat is released
// when a newer one pins). A key pair on screen counts as one of the 2: then the opening stays if it holds a
// qualifier, else the latest qualifier beat, else the opening.
// No overlap: the beat leaving is gone (EXIT_S) before the next one starts to appear (ENTER_DELAY).
import type { Line } from "./types";

export const EXIT_S = 0.12;
export const ENTER_DELAY = 0.12;
const QUALIFIER = /\b(no|not|never|without|only|may|might|could|associated|among)\b|\bin this study\b/i;

export type OnScreen = { hold: boolean; pinned: number[]; current: number | null; exiting: number | null };

export const pinnedSet = (line: Line): Set<number> =>
  new Set(line.kind !== "health" ? [] : line.beats.flatMap((b, k) => (k === 0 || QUALIFIER.test(b.text) ? [k] : [])));

export const onScreen = (line: Line, t: number, keyShown = false): OnScreen | null => {
  if (t < line.start || t >= line.hold_end) return null;
  if (t >= line.end) return { hold: true, pinned: [], current: null, exiting: null };
  let i = 0;
  line.beats.forEach((b, k) => { if (b.start <= t) i = k; });
  const pins = pinnedSet(line);
  const said = [...pins].filter((k) => k < i).sort((a, b) => a - b);
  const opening = said.includes(0) ? 0 : null;
  const quals = said.filter((k) => k !== 0);
  const lastQual = quals.length ? quals[quals.length - 1] : null;
  let pinned: number[];
  if (!keyShown) pinned = [opening, lastQual].filter((k): k is number => k !== null);
  else if (opening !== null && QUALIFIER.test(line.beats[0].text)) pinned = [0];
  else pinned = lastQual !== null ? [lastQual] : opening !== null ? [0] : [];
  const prev = i - 1;
  const exiting = prev >= 0 && !pins.has(prev) && t - line.beats[i].start < EXIT_S ? prev : null;
  return { hold: false, pinned, current: i, exiting };
};

/** The strings the line puts on screen at t (the hold draws the whole checked line). */
export const visibleTexts = (line: Line, t: number, keyShown = false): string[] => {
  const s = onScreen(line, t, keyShown);
  if (!s) return [];
  if (s.hold) return [line.text];
  return [...s.pinned, ...(s.current !== null ? [s.current] : []), ...(s.exiting !== null ? [s.exiting] : [])]
    .map((k) => line.beats[k].text);
};
