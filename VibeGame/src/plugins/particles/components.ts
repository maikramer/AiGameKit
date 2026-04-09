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
