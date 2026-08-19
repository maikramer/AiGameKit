import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const ThirdPersonCamera = defineComponent({
  // Target entity to follow (usually the player)
  target: U32,
  // Distance behind the target
  distance: F32,
  // Height above target
  height: F32,
  // Horizontal angle (yaw) in radians — updated by mouse
  yaw: F32,
  // Vertical angle (pitch) in radians — updated by mouse
  pitch: F32,
  // How fast the camera follows position (0-1, lower = more lag)
  positionSmooth: F32,
  // Mouse sensitivity
  mouseSensitivity: F32,
  // Current smoothed camera position (internal)
  currentX: F32,
  currentY: F32,
  currentZ: F32,
  // Whether the camera has been initialized
  initialized: U8,
  // Minimum height above terrain surface (0 = disabled)
  minTerrainDistance: F32,
  // --- Decoupled follow (internal) ---
  // Smoothed follow point the camera orbits & looks at. Decoupled from the raw
  // character transform so the view never shakes even if the character does.
  followX: F32,
  followY: F32,
  followZ: F32,
  // Smoothed (lagged) yaw the camera orbits at; trails the steered heading.
  smoothYaw: F32,
  // Position follow time constant in seconds (larger = more lag on dashes).
  followLag: F32,
  // Yaw follow time constant in seconds (larger = camera turns slower/later).
  turnLag: F32,
});
