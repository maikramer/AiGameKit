import * as THREE from 'three';
import type {
  VariationGeometryInput,
  VariationSample,
  VariationVisualSpec,
} from './types';

const _color = new THREE.Color(1, 1, 1);

function lerpRange(lo: number, hi: number, t: number): number {
  return lo + (hi - lo) * t;
}

function pickUniform(lo: number, hi: number, rand: () => number): number {
  if (lo === hi) return lo;
  return lerpRange(lo, hi, rand());
}

/** Stable hash of world XZ → [0, 1). */
export function hashWorldXZ(wx: number, wz: number): number {
  const ix = Math.floor(wx * 10.0) | 0;
  const iz = Math.floor(wz * 10.0) | 0;
  let h = (ix * 374761393 + iz * 668265263) | 0;
  h = (h ^ (h >>> 13)) * 1274126177;
  h = h ^ (h >>> 16);
  return (h >>> 0) / 4294967296;
}

function blendRand(seq: number, spatial: number, mix: number): number {
  if (mix <= 0) return seq;
  if (mix >= 1) return spatial;
  return seq * (1 - mix) + spatial * mix;
}

function pickScaleUniform(
  geom: VariationGeometryInput,
  rand: () => number
): number {
  if (
    geom.scaleDistribution === 'discrete' &&
    geom.scaleDiscreteValues.length > 0
  ) {
    const arr = geom.scaleDiscreteValues;
    return arr[Math.floor(rand() * arr.length)]!;
  }
  return pickUniform(geom.scaleMin, geom.scaleMax, rand);
}

function pickAxis(geom: VariationGeometryInput, rand: () => number): number {
  return pickUniform(geom.scaleAxisMin, geom.scaleAxisMax, rand);
}

function pickYawRad(geom: VariationGeometryInput, rand: () => number): number {
  if (!geom.randomYaw) return 0;
  if (geom.yawDistribution === 'discrete' && geom.yawDiscreteDeg.length > 0) {
    const arr = geom.yawDiscreteDeg;
    const deg = arr[Math.floor(rand() * arr.length)]!;
    return (deg * Math.PI) / 180;
  }
  return rand() * Math.PI * 2;
}

function isIdentityVisual(visual: VariationVisualSpec): boolean {
  return (
    visual.hueJitterDeg === 0 &&
    visual.saturationMin === 1 &&
    visual.saturationMax === 1 &&
    visual.brightnessMin === 1 &&
    visual.brightnessMax === 1 &&
    visual.contrastMin === 1 &&
    visual.contrastMax === 1
  );
}

/**
 * Soft multiplicative RGB tint from hue/saturation jitter.
 * Normalised so the mean channel stays ~1 (brightness handled separately).
 */
function sampleTintRgb(
  hueJitterDeg: number,
  satMin: number,
  satMax: number,
  tHue: number,
  tSat: number
): { r: number; g: number; b: number } {
  if (hueJitterDeg === 0 && satMin === 1 && satMax === 1) {
    return { r: 1, g: 1, b: 1 };
  }
  const hue01 = (((tHue * 2 - 1) * hueJitterDeg) / 360 + 1) % 1;
  const satMul = lerpRange(satMin, satMax, tSat);
  // Pastel HSL tint, then soften toward white. Strength scales with authored
  // hue jitter so foliage (±8°) reads clearly and rocks (±3°) stay subtle.
  const sat = Math.min(1, Math.max(0, 0.22 + Math.abs(satMul - 1) * 0.7));
  _color.setHSL(hue01, sat, 0.62);
  const avg = (_color.r + _color.g + _color.b) / 3 || 1;
  let r = _color.r / avg;
  let g = _color.g / avg;
  let b = _color.b / avg;
  const strength = Math.min(
    0.85,
    Math.max(0.2, hueJitterDeg / 14 + Math.abs(satMul - 1) * 0.55)
  );
  r = 1 + (r - 1) * strength;
  g = 1 + (g - 1) * strength;
  b = 1 + (b - 1) * strength;
  return { r, g, b };
}

/**
 * Sample geometry + visual variation for one spawn instance.
 * Geometry draws consume `rand` first (stable with prior spawn order);
 * visual channels may blend in a world-XZ hash.
 */
export function sampleVariation(
  geom: VariationGeometryInput,
  visual: VariationVisualSpec,
  rand: () => number,
  wx: number,
  wz: number
): VariationSample {
  const scaleUniform = pickScaleUniform(geom, rand);
  const axisX = pickAxis(geom, rand);
  const axisY = pickAxis(geom, rand);
  const axisZ = pickAxis(geom, rand);
  const yawRad = pickYawRad(geom, rand);

  if (isIdentityVisual(visual)) {
    return {
      scaleUniform,
      axisX,
      axisY,
      axisZ,
      yawRad,
      colorR: 1,
      colorG: 1,
      colorB: 1,
      brightness: 1,
      contrast: 1,
    };
  }

  const spatial = Math.min(1, Math.max(0, visual.spatial));
  const tHue = blendRand(rand(), hashWorldXZ(wx, wz), spatial);
  const tSat = blendRand(rand(), hashWorldXZ(wx + 17.3, wz - 9.1), spatial);
  const tBright = blendRand(rand(), hashWorldXZ(wx - 5.7, wz + 13.9), spatial);
  const tContrast = blendRand(
    rand(),
    hashWorldXZ(wx + 3.1, wz + 21.5),
    spatial
  );

  const tint = sampleTintRgb(
    visual.hueJitterDeg,
    visual.saturationMin,
    visual.saturationMax,
    tHue,
    tSat
  );

  return {
    scaleUniform,
    axisX,
    axisY,
    axisZ,
    yawRad,
    colorR: tint.r,
    colorG: tint.g,
    colorB: tint.b,
    brightness: lerpRange(visual.brightnessMin, visual.brightnessMax, tBright),
    contrast: lerpRange(visual.contrastMin, visual.contrastMax, tContrast),
  };
}
