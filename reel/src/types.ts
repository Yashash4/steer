export type Ground = "cream" | "rust" | "maroon";
export type Palette = Record<"cream" | "rust" | "maroon" | "ink" | "clay" | "blush" | "paper", string>;
export type Word = { text: string; start: number };
export type Beat = { text: string; start: number; words: Word[] };
export type CardPart = { text: string; role: "setting" | "hero" | "text"; show: string; at: number };
export type Card = { start: number; parts: CardPart[] };
export type Key = { number: string; label: string; at: number };
export type Line = {
  kind: "health" | "framing" | "brand" | "cta" | "signoff";
  text: string; start: number; end: number; hold_end: number;
  beats: Beat[]; cards: Card[] | null; emphasis: string | null; key: Key | null; source_label: string | null;
};
export type Sources = {
  heading: string; entries: { before: string; journal: string; after: string }[]; footnote: string; url: string;
};
export type Presenter = { video: string; start: number; end: number; label: string };
export type Scene = {
  kind: "hook" | "point" | "brand" | "cta" | "sources"; ground: Ground; start: number; end: number;
  transition_in: "wipe" | "none"; broll: null; presenter: Presenter | null; lines: Line[]; sources: Sources | null;
};
export type ReelProps = {
  version: 2; fps: 30; width: 1080; height: 1920; durationInFrames: number; audio: string;
  fonts: { serif: string; serif_italic: string; sans: string };
  palette: Palette; chrome: { draft_mark: string; wordmark: string }; scenes: Scene[];
};
