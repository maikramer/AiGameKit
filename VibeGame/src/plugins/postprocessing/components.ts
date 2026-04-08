import { defineComponent, Types } from 'bitecs';

export const Bloom = defineComponent({
  intensity: Types.f32,
  luminanceThreshold: Types.f32,
  luminanceSmoothing: Types.f32,
  mipmapBlur: Types.ui8,
  radius: Types.f32,
  levels: Types.ui8,
});

export const Dithering = defineComponent({
  colorBits: Types.ui8,
  intensity: Types.f32,
  grayscale: Types.ui8,
  scale: Types.f32,
  noise: Types.f32,
});

export const Tonemapping = defineComponent({
  mode: Types.ui8,
  middleGrey: Types.f32,
  whitePoint: Types.f32,
  averageLuminance: Types.f32,
  adaptationRate: Types.f32,
});

export const SMAA = defineComponent({
  preset: Types.ui8,
});

export const Vignette = defineComponent({
  darkness: Types.f32,
  offset: Types.f32,
});

export const DepthOfField = defineComponent({
  focusDistance: Types.f32,
  focalLength: Types.f32,
  bokehScale: Types.f32,
  resolutionScale: Types.f32,
  autoFocus: Types.ui8,
});

export const ChromaticAberration = defineComponent({
  offsetX: Types.f32,
  offsetY: Types.f32,
  radialModulation: Types.ui8,
  modulationOffset: Types.f32,
});

export const Noise = defineComponent({
  opacity: Types.f32,
  blendFunction: Types.ui8,
});
