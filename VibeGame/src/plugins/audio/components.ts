import { defineComponent, Types } from 'bitecs';

export const AudioSource = defineComponent({
  clipPath: Types.ui32, // string ID
  volume: Types.f32,
  loop: Types.ui8,
  pitch: Types.f32,
  spatial: Types.ui8, // 0 = 2D, 1 = 3D
  minDistance: Types.f32, // for 3D
  maxDistance: Types.f32, // for 3D
  rolloff: Types.f32, // for 3D
  playing: Types.ui8,
});

export const AudioListener = defineComponent({
  posX: Types.f32,
  posY: Types.f32,
  posZ: Types.f32,
});
