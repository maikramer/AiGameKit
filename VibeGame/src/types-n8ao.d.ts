declare module 'n8ao' {
  import type { Camera, Color, Scene } from 'three';
  import type { Pass } from 'postprocessing';

  export interface N8AOConfiguration {
    aoSamples: number;
    aoRadius: number;
    denoiseSamples: number;
    denoiseRadius: number;
    distanceFalloff: number;
    intensity: number;
    renderMode: 0 | 1 | 2 | 3 | 4;
    color: Color;
    gammaCorrection: boolean;
    transparencyAware: boolean;
    halfRes: boolean;
    screenSpaceRadius: boolean;
  }

  export class N8AOPostPass extends Pass {
    constructor(scene: Scene, camera: Camera, width?: number, height?: number);
    configuration: N8AOConfiguration;
    setDisplayMode(
      mode: 'Combined' | 'AO' | 'No AO' | 'Split' | 'Split AO'
    ): void;
    setQualityMode(
      mode: 'Performance' | 'Low' | 'Medium' | 'High' | 'Ultra'
    ): void;
    setSize(width: number, height: number): void;
  }
}
