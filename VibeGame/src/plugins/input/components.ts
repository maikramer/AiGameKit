import { MAX_ENTITIES } from '../../core/ecs/constants';

export const InputState = {
  moveX: new Float32Array(MAX_ENTITIES),
  moveY: new Float32Array(MAX_ENTITIES),
  moveZ: new Float32Array(MAX_ENTITIES),
  lookX: new Float32Array(MAX_ENTITIES),
  lookY: new Float32Array(MAX_ENTITIES),
  scrollDelta: new Float32Array(MAX_ENTITIES),
  jump: new Uint8Array(MAX_ENTITIES),
  primaryAction: new Uint8Array(MAX_ENTITIES),
  secondaryAction: new Uint8Array(MAX_ENTITIES),
  leftMouse: new Uint8Array(MAX_ENTITIES),
  rightMouse: new Uint8Array(MAX_ENTITIES),
  middleMouse: new Uint8Array(MAX_ENTITIES),
  jumpBufferTime: new Float32Array(MAX_ENTITIES),
  primaryBufferTime: new Float32Array(MAX_ENTITIES),
  secondaryBufferTime: new Float32Array(MAX_ENTITIES),
} as const;
