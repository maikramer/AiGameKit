import { defineComponent, F32, U32 } from '../../core/ecs/component-storage';

export const OrbitCamera = defineComponent({
  target: U32,
  inputSource: U32,
  currentYaw: F32,
  currentPitch: F32,
  currentDistance: F32,
  targetYaw: F32,
  targetPitch: F32,
  targetDistance: F32,
  minDistance: F32,
  maxDistance: F32,
  minPitch: F32,
  maxPitch: F32,
  smoothness: F32,
  offsetX: F32,
  offsetY: F32,
  offsetZ: F32,
  sensitivity: F32,
  zoomSensitivity: F32,
});
