import { Composition } from "remotion";
import { Reel } from "./Reel";
import { LineArt } from "./LineArt";
import type { ReelProps } from "./types";

export const Root = () => (
  <>
  <Composition id="LineArtTest" component={LineArt} width={1080} height={1350} fps={30} durationInFrames={90} />
  <Composition
    id="Reel"
    component={Reel}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={1}
    defaultProps={{} as ReelProps}
    calculateMetadata={({ props }) => ({ durationInFrames: props.durationInFrames })}
  />
  </>
);
