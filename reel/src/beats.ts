// Which phrases of a line are on screen at time t. Pure, so test/beats.mjs can check every frame of a line.
//
// Meaning safety: a still frame must never read as a fragment ("back when the medicine / stops?") or the opposite
// of the claim. So nothing leaves the screen mid-line:
// - a health line BUILDS UP: each phrase (the beat split) appends below the earlier ones when it is spoken, and every
//   phrase stays until the line's hold ends. Every frame shows a prefix of the line made of whole phrases.
// - a framing, brand, cta or signoff line is shown COMPLETE from the moment it appears (`appear`: the scene's first
//   frame for a scene's first line, else the line's start); only the spoken-word highlight moves.
import type { Line } from "./types";

export const DIM = 0.72;   // earlier phrases stay readable

export type OnScreen = { shown: number; current: number | null };

export const buildsUp = (line: Line) => line.kind === "health";

export const onScreen = (line: Line, t: number, appear: number = line.start): OnScreen | null => {
  if (t >= line.hold_end) return null;
  const speaking = t >= line.start && t < line.end;
  let i = 0;
  line.beats.forEach((b, k) => { if (b.start <= t) i = k; });
  if (!buildsUp(line)) return t < appear ? null : { shown: line.beats.length, current: speaking ? i : null };
  if (t < line.start) return null;
  return speaking ? { shown: i + 1, current: i } : { shown: line.beats.length, current: null };
};

/** The phrases the line puts on screen at t, in order. */
export const visibleTexts = (line: Line, t: number, appear: number = line.start): string[] => {
  const s = onScreen(line, t, appear);
  return s ? line.beats.slice(0, s.shown).map((b) => b.text) : [];
};
