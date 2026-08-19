import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const AudioSource = defineComponent({
  clipPath: U32,
  volume: F32,
  loop: U8,
  pitch: F32,
  spatial: U8,
  minDistance: F32,
  maxDistance: F32,
  rolloff: F32,
  playing: U8,
});

export const AudioListener = defineComponent({
  posX: F32,
  posY: F32,
  posZ: F32,
});

export const MusicLayerComponent = defineComponent({
  layer: U8,
  volume: F32,
  fade: F32,
});
