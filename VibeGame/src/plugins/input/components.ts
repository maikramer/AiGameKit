import { defineComponent, F32, U8 } from '../../core/ecs/component-storage';

export const InputState = defineComponent({
  moveX: F32,
  moveY: F32,
  moveZ: F32,
  lookX: F32,
  lookY: F32,
  scrollDelta: F32,
  jump: U8,
  sprint: U8,
  primaryAction: U8,
  secondaryAction: U8,
  leftMouse: U8,
  rightMouse: U8,
  middleMouse: U8,
  jumpBufferTime: F32,
  primaryBufferTime: F32,
  secondaryBufferTime: F32,
});

export const GamepadInput = defineComponent({
  connected: U8,
  deadzone: F32,
  leftStickX: F32,
  leftStickY: F32,
  rightStickX: F32,
  rightStickY: F32,
  buttonA: U8,
  buttonB: U8,
  buttonX: U8,
  buttonY: U8,
  leftBumper: U8,
  rightBumper: U8,
  leftTrigger: F32,
  rightTrigger: F32,
});
