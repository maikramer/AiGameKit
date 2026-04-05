import { defineComponent, Types } from 'bitecs';

export const FollowCamera = defineComponent({
  target: Types.eid,
  inputSource: Types.eid,

  currentYaw: Types.f32,
  currentPitch: Types.f32,
  currentDistance: Types.f32,

  targetYaw: Types.f32,
  targetPitch: Types.f32,
  targetDistance: Types.f32,

  minDistance: Types.f32,
  maxDistance: Types.f32,
  minPitch: Types.f32,
  maxPitch: Types.f32,

  /** Smoothing factor for position/pitch/zoom (0..1, higher = snappier). */
  smoothness: Types.f32,
  /** Separate smoothing for yaw auto-rotation (typically slower for cinematic feel). */
  yawSmoothness: Types.f32,

  offsetX: Types.f32,
  offsetY: Types.f32,
  offsetZ: Types.f32,

  zoomSensitivity: Types.f32,

  /** 1 = camera yaw auto-tracks the character's facing direction. */
  autoRotate: Types.ui8,
  /** Delay (seconds) after manual input before auto-rotate resumes. */
  autoRotateDelay: Types.f32,
  lastManualInputTime: Types.f32,

  /** 1 = right-mouse-drag still works for manual orbit override. */
  allowManualOrbit: Types.ui8,
  sensitivity: Types.f32,

  /** Smoothing for the look-at target position (lower = more lag behind player). */
  positionLag: Types.f32,
  /** Smoothed look-at position (internal, updated each frame). */
  smoothedTargetX: Types.f32,
  smoothedTargetY: Types.f32,
  smoothedTargetZ: Types.f32,
  /** Set to 1 after first frame to indicate smoothed target is initialized. */
  smoothedTargetInit: Types.ui8,

  /** Current zoom preset index (0 = close, 1 = medium, 2 = far). */
  zoomLevel: Types.ui8,
  /** Tracks whether the zoom toggle key was held last frame (for edge detection). */
  zoomKeyHeld: Types.ui8,
});
