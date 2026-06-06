import { MAX_ENTITIES } from '../../core/ecs/constants';

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

export const TweenData = {
  targetEntity: new Uint32Array(MAX_ENTITIES),
  axis: new Uint8Array(MAX_ENTITIES),
  from: new Float32Array(MAX_ENTITIES),
  to: new Float32Array(MAX_ENTITIES),
  duration: new Float32Array(MAX_ENTITIES),
  delay: new Float32Array(MAX_ENTITIES),
  easing: new Uint8Array(MAX_ENTITIES),
  loop: new Uint8Array(MAX_ENTITIES),
  pingPong: new Uint8Array(MAX_ENTITIES),
  elapsed: new Float32Array(MAX_ENTITIES),
  active: new Uint8Array(MAX_ENTITIES),
} as const;
