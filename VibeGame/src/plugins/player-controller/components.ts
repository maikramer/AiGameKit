import { MAX_ENTITIES } from '../../core/ecs/constants';

export const ThirdPersonCamera = {
  // Target entity to follow (usually the player)
  target: new Uint32Array(MAX_ENTITIES),
  // Distance behind the target
  distance: new Float32Array(MAX_ENTITIES),
  // Height above target
  height: new Float32Array(MAX_ENTITIES),
  // Horizontal angle (yaw) in radians — updated by mouse
  yaw: new Float32Array(MAX_ENTITIES),
  // Vertical angle (pitch) in radians — updated by mouse
  pitch: new Float32Array(MAX_ENTITIES),
  // How fast the camera follows position (0-1, lower = more lag)
  positionSmooth: new Float32Array(MAX_ENTITIES),
  // Mouse sensitivity
  mouseSensitivity: new Float32Array(MAX_ENTITIES),
  // Current smoothed camera position (internal)
  currentX: new Float32Array(MAX_ENTITIES),
  currentY: new Float32Array(MAX_ENTITIES),
  currentZ: new Float32Array(MAX_ENTITIES),
  // Whether the camera has been initialized
  initialized: new Uint8Array(MAX_ENTITIES),
} as const;
