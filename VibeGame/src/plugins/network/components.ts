import { defineComponent, Types } from 'bitecs';

export const Networked = defineComponent({
  networkId: Types.ui32,
  isOwner: Types.ui8,
  interpolate: Types.ui8,
});

export const NetworkBuffer = defineComponent({
  prevX: Types.f32,
  prevY: Types.f32,
  prevZ: Types.f32,
  nextX: Types.f32,
  nextY: Types.f32,
  nextZ: Types.f32,
});
