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
  // Minimum height above terrain surface (0 = disabled)
  minTerrainDistance: new Float32Array(MAX_ENTITIES),
  // --- Decoupled follow (internal) ---
  // Smoothed follow point the camera orbits & looks at. Decoupled from the raw
  // character transform so the view never shakes even if the character does.
  followX: new Float32Array(MAX_ENTITIES),
  followY: new Float32Array(MAX_ENTITIES),
  followZ: new Float32Array(MAX_ENTITIES),
  // Smoothed (lagged) yaw the camera orbits at; trails the steered heading.
  smoothYaw: new Float32Array(MAX_ENTITIES),
  // Position follow time constant in seconds (larger = more lag on dashes).
  followLag: new Float32Array(MAX_ENTITIES),
  // Yaw follow time constant in seconds (larger = camera turns slower/later).
  turnLag: new Float32Array(MAX_ENTITIES),
} as const;
