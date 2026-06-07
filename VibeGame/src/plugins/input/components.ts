import { MAX_ENTITIES } from '../../core/ecs/constants';

export const InputState = {
  moveX: new Float32Array(MAX_ENTITIES),
  moveY: new Float32Array(MAX_ENTITIES),
  moveZ: new Float32Array(MAX_ENTITIES),
  lookX: new Float32Array(MAX_ENTITIES),
  lookY: new Float32Array(MAX_ENTITIES),
  scrollDelta: new Float32Array(MAX_ENTITIES),
  jump: new Uint8Array(MAX_ENTITIES),
  sprint: new Uint8Array(MAX_ENTITIES),
  primaryAction: new Uint8Array(MAX_ENTITIES),
  secondaryAction: new Uint8Array(MAX_ENTITIES),
  leftMouse: new Uint8Array(MAX_ENTITIES),
  rightMouse: new Uint8Array(MAX_ENTITIES),
  middleMouse: new Uint8Array(MAX_ENTITIES),
  jumpBufferTime: new Float32Array(MAX_ENTITIES),
  primaryBufferTime: new Float32Array(MAX_ENTITIES),
  secondaryBufferTime: new Float32Array(MAX_ENTITIES),
} as const;

export const GamepadInput = {
  connected: new Uint8Array(MAX_ENTITIES),
  deadzone: new Float32Array(MAX_ENTITIES),
  leftStickX: new Float32Array(MAX_ENTITIES),
  leftStickY: new Float32Array(MAX_ENTITIES),
  rightStickX: new Float32Array(MAX_ENTITIES),
  rightStickY: new Float32Array(MAX_ENTITIES),
  buttonA: new Uint8Array(MAX_ENTITIES),
  buttonB: new Uint8Array(MAX_ENTITIES),
  buttonX: new Uint8Array(MAX_ENTITIES),
  buttonY: new Uint8Array(MAX_ENTITIES),
  leftBumper: new Uint8Array(MAX_ENTITIES),
  rightBumper: new Uint8Array(MAX_ENTITIES),
  leftTrigger: new Float32Array(MAX_ENTITIES),
  rightTrigger: new Float32Array(MAX_ENTITIES),
} as const;
