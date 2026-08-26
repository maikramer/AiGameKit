import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

/**
 * Fixed-angle isometric camera: orthographic projection, a pitch that never
 * changes, and a yaw locked to four 90°-apart headings.
 *
 * Unlike {@link ../orbit-camera OrbitCamera} there is no free look — the whole
 * point is that the world always reads at the same angle, so authored art and
 * a tile grid line up frame after frame. What the player controls is which of
 * the four quadrants is "up" (Q/E) and how much of the world fits on screen
 * (scroll → `orthoSize`).
 */
export const IsometricCamera = defineComponent({
  /** Entity the camera follows (usually the player). 0 = unbound. */
  target: U32,
  /** Entity whose `InputState` drives Q/E and scroll. 0 = auto-resolve. */
  inputSource: U32,

  // --- Yaw ---
  /** Quadrant index 0..3. Purely informational; `targetYaw` is the authority. */
  yawIndex: U8,
  /** Smoothed yaw actually used for the pose (radians, unwrapped). */
  yaw: F32,
  /**
   * Yaw the camera is settling towards (radians, unwrapped).
   *
   * Q/E **accumulate** `±rotateStep` here instead of recomputing
   * `yawIndex * π/2`. That is what keeps every rotation a 90° short path: a
   * wrapped target would make the 3 → 0 step look like −270°.
   */
  targetYaw: F32,
  /** Radians added per Q/E press. Default π/2. */
  rotateStep: F32,
  /** 0 disables Q/E entirely (a permanently fixed view). */
  allowRotate: U8,
  /** Edge latches so a held Q/E rotates once, not once per frame. */
  qHeld: U8,
  eHeld: U8,

  // --- Pose ---
  /** Fixed elevation angle (radians). Default `atan(1/√2)` = true isometric. */
  pitch: F32,
  /**
   * Length of the camera arm (m). With an orthographic projection this does
   * not change the framing — `orthoSize` does — but it decides how much world
   * sits between the near and far planes, so keep it comfortably large.
   */
  distance: F32,

  // --- Zoom (orthographic frustum height, in metres) ---
  orthoSize: F32,
  targetOrthoSize: F32,
  minOrthoSize: F32,
  maxOrthoSize: F32,
  zoomSensitivity: F32,

  // --- Follow ---
  /** Position follow time constant (s); larger = the camera trails further. */
  followLag: F32,
  /** Vertical follow time constant (s); damped harder to swallow step bob. */
  followLagY: F32,
  /** Yaw follow time constant (s) for the Q/E rotation sweep. */
  turnLag: F32,
  /** Offset added to the follow point (m). */
  offsetX: F32,
  offsetY: F32,
  offsetZ: F32,
  /** Smoothed follow point (internal). */
  followX: F32,
  followY: F32,
  followZ: F32,
  /** 0 until the first frame has snapped the smoothed state onto the target. */
  initialized: U8,
});
