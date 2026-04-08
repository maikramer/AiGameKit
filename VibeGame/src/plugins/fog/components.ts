import { defineComponent, Types } from 'bitecs';

export const Fog = defineComponent({
  mode: Types.f32,
  density: Types.f32,
  near: Types.f32,
  far: Types.f32,
  colorR: Types.f32,
  colorG: Types.f32,
  colorB: Types.f32,
  heightFalloff: Types.f32,
  baseHeight: Types.f32,
  volumetricStrength: Types.f32,
  quality: Types.f32,
  noiseScale: Types.f32,
});
