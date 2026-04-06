import { defineComponent, Types } from 'bitecs';

export const Lod = defineComponent({
  near: Types.f32,
  far: Types.f32,
  currentLevel: Types.ui8, // 0=near, 1=far
  nearEntity: Types.eid,
  farEntity: Types.eid,
});
