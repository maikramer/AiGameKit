import { defineComponent, Types } from 'bitecs';

/** Connection status: 0=disconnected, 1=connecting, 2=connected, 3=error. */
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
  nextX: Types.f32,
  nextY: Types.f32,
  nextZ: Types.f32,
});
