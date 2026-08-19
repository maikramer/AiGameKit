import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export enum TweenAxis {
  None = 0,
  PosX = 1,
  PosY = 2,
  PosZ = 3,
  RotX = 4,
  RotY = 5,
  RotZ = 6,
}

export enum EasingType {
  Linear = 0,
  EaseInOut = 1,
  EaseOutQuad = 2,
}

export const TweenData = defineComponent({
  targetEntity: U32,
  axis: U8,
  from: F32,
  to: F32,
  duration: F32,
  delay: F32,
  easing: U8,
  loop: U8,
  pingPong: U8,
  elapsed: F32,
  active: U8,
});
