declare module 'screen-space-reflections' {
  import { Camera, Scene } from 'three';
  import { Effect } from 'postprocessing';

  interface SSROptions {
    intensity?: number;
    exponent?: number;
    distance?: number;
    fade?: number;
    roughnessFade?: number;
    thickness?: number;
    ior?: number;
    maxRoughness?: number;
    maxDepthDifference?: number;
    blend?: number;
    correction?: number;
    correctionRadius?: number;
    blur?: number;
    blurKernel?: number;
    blurSharpness?: number;
    jitter?: number;
    jitterRoughness?: number;
    steps?: number;
    refineSteps?: number;
    missedRays?: boolean;
    useNormalMap?: boolean;
    useRoughnessMap?: boolean;
    resolutionScale?: number;
    velocityResolutionScale?: number;
  }

  export class SSREffect extends Effect {
    intensity: number;
    distance: number;
    constructor(scene: Scene, camera: Camera, options?: SSROptions);
  }
}
