import { defineComponent, Types } from 'bitecs';

export const Terrain = defineComponent({
  worldSize: Types.f32,
  maxHeight: Types.f32,
  levels: Types.ui8,
  resolution: Types.ui8,
  lodDistanceRatio: Types.f32,
  wireframe: Types.ui8,
});
