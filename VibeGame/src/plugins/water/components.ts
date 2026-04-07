import { defineComponent, Types } from 'bitecs';

export const Water = defineComponent({
  size: Types.f32,
  waterLevel: Types.f32,
  opacity: Types.f32,
  tintR: Types.f32,
  tintG: Types.f32,
  tintB: Types.f32,
  waveSpeed: Types.f32,
  waveScale: Types.f32,
  wireframe: Types.ui8,
  underwaterFogColorR: Types.f32,
  underwaterFogColorG: Types.f32,
  underwaterFogColorB: Types.f32,
  underwaterFogDensity: Types.f32,
});
