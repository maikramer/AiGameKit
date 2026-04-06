import { defineComponent, Types } from 'bitecs';

export const Sprite = defineComponent({
  texturePath: Types.ui32, // string ID from StringStore
  width: Types.f32,
  height: Types.f32,
  pivotX: Types.f32,
  pivotY: Types.f32,
  opacity: Types.f32,
  colorR: Types.f32,
  colorG: Types.f32,
  colorB: Types.f32,
  flipX: Types.ui8,
  flipY: Types.ui8,
  layer: Types.ui16, // render order
});
