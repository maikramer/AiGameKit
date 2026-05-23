import { MAX_ENTITIES } from '../../core/ecs/constants';

export const OrbitCamera = {
  target: new Uint32Array(MAX_ENTITIES),
  inputSource: new Uint32Array(MAX_ENTITIES),
  currentYaw: new Float32Array(MAX_ENTITIES),
  currentPitch: new Float32Array(MAX_ENTITIES),
  currentDistance: new Float32Array(MAX_ENTITIES),
  targetYaw: new Float32Array(MAX_ENTITIES),
  targetPitch: new Float32Array(MAX_ENTITIES),
  targetDistance: new Float32Array(MAX_ENTITIES),
  minDistance: new Float32Array(MAX_ENTITIES),
  maxDistance: new Float32Array(MAX_ENTITIES),
  minPitch: new Float32Array(MAX_ENTITIES),
  maxPitch: new Float32Array(MAX_ENTITIES),
  smoothness: new Float32Array(MAX_ENTITIES),
  offsetX: new Float32Array(MAX_ENTITIES),
  offsetY: new Float32Array(MAX_ENTITIES),
  offsetZ: new Float32Array(MAX_ENTITIES),
  sensitivity: new Float32Array(MAX_ENTITIES),
  zoomSensitivity: new Float32Array(MAX_ENTITIES),
} as const;
