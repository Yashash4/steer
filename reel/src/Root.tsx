import { Composition } from "remotion";
import { Reel } from "./Reel";
import type { ReelProps } from "./types";

export const Root = () => (
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
);
