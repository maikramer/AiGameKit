import { defineComponent, Types } from 'bitecs';

export const NetworkStatus = defineComponent({
  connected: Types.ui8,
});

export const Networked = defineComponent({
  networkId: Types.ui32,
  isOwner: Types.ui8,
  interpolate: Types.ui8,
});

export const NetworkBuffer = defineComponent({
  prevX: Types.f32,
  prevY: Types.f32,
  prevZ: Types.f32,
  prevRotX: Types.f32,
  prevRotY: Types.f32,
  prevRotZ: Types.f32,
  prevRotW: Types.f32,
  prevScaleX: Types.f32,
  prevScaleY: Types.f32,
  prevScaleZ: Types.f32,
  nextX: Types.f32,
  nextY: Types.f32,
  nextZ: Types.f32,
  nextRotX: Types.f32,
  nextRotY: Types.f32,
  nextRotZ: Types.f32,
  nextRotW: Types.f32,
  nextScaleX: Types.f32,
  nextScaleY: Types.f32,
  nextScaleZ: Types.f32,
});
