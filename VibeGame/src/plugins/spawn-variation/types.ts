/** Named visual-variation presets for spawn groups. */
export type VariationPresetId = 'none' | 'tree' | 'foliage' | 'rock';

/**
 * Visual channels sampled per instance (geometry stays on SpawnGroupSpec).
 * Ranges are inclusive; hue is ± degrees around the authored albedo.
 */
export interface VariationVisualSpec {
  preset: VariationPresetId;
  /** Max absolute hue shift in degrees (uniform in `[-hue, +hue]`). */
  hueJitterDeg: number;
  saturationMin: number;
  saturationMax: number;
  brightnessMin: number;
  brightnessMax: number;
  contrastMin: number;
  contrastMax: number;
  /**
   * 0 = sequential PRNG only; 1 = colour channels driven by world-XZ hash.
   * Values in between blend the two streams (breaks sequential banding).
   */
  spatial: number;
}

/** One draw of geometry + visual variation for a spawn instance. */
export interface VariationSample {
  scaleUniform: number;
  axisX: number;
  axisY: number;
  axisZ: number;
  yawRad: number;
  /** Multiplicative RGB tint (from HSV jitter), typically near white. */
  colorR: number;
  colorG: number;
  colorB: number;
  brightness: number;
  contrast: number;
}

/** Geometry knobs consumed by {@link sampleVariation} (from SpawnGroupSpec). */
export interface VariationGeometryInput {
  randomYaw: boolean;
  scaleDistribution: 'linear' | 'discrete';
  scaleDiscreteValues: number[];
  scaleMin: number;
  scaleMax: number;
  scaleAxisMin: number;
  scaleAxisMax: number;
  yawDistribution: 'linear' | 'discrete';
  yawDiscreteDeg: number[];
}
