import { defineComponent, Types } from 'bitecs';

/** Estado do emissor declarativo (preset + parâmetros). */
export const ParticlesEmitter = defineComponent({
  preset: Types.ui8,
  rate: Types.f32,
  lifetime: Types.f32,
  size: Types.f32,
  looping: Types.ui8,
  playing: Types.ui8,
  spawned: Types.ui8,
});

export const ParticlesBurst = defineComponent({
  preset: Types.ui8,
  count: Types.f32,
  triggered: Types.ui8,
});

export const ColorOverLife = defineComponent({
  startR: Types.f32,
  startG: Types.f32,
  startB: Types.f32,
  startA: Types.f32,
  endR: Types.f32,
  endG: Types.f32,
  endB: Types.f32,
  endA: Types.f32,
});

export const SizeOverLife = defineComponent({
  startSize: Types.f32,
  endSize: Types.f32,
});

export const ParticleTexture = defineComponent({
  frameWidth: Types.f32,
  frameHeight: Types.f32,
  frames: Types.ui8,
  animationSpeed: Types.f32,
});
