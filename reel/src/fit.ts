import { fitTextOnNLines } from "@remotion/layout-utils";

export const SERIF = "ReelSerif";
export const SERIF_ITALIC = "ReelSerifItalic";
export const SANS = "ReelSans";
export const SERIF_VARS = "'WONK' 0, 'SOFT' 50";

// Fits text; throws (the render fails) when it would be smaller than minSize or does not fit at all.
export const fit = (text: string, family: string, weight: number, maxLines: number, box: number, maxSize: number,
  minSize: number) => {
  const r = fitTextOnNLines({
    text, maxLines, maxBoxWidth: box, fontFamily: family, fontWeight: weight, maxFontSize: maxSize,
    validateFontIsLoaded: true,
    additionalStyles: family === SANS ? {} : { fontVariationSettings: SERIF_VARS },
  });
  if (r.lines.length === 0 || r.fontSize < minSize) {
    throw new Error("text does not fit at " + minSize + "px: " + text);
  }
  return r;
};
