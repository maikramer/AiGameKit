import { IsometricCamera } from './components';

/**
 * True isometric elevation: `atan(1/√2)` ≈ 35.264°. At this angle a unit cube
 * projects to a regular hexagon, which is what makes an orthographic view read
 * as "isometric" rather than merely "tilted".
 */
export const ISO_PITCH = Math.atan(1 / Math.SQRT2);

/** Time constant (s) of the zoom low-pass. */
const ZOOM_SMOOTH_TIME = 0.12;

/**
 * Advance a quadrant index by one step, wrapping 0..3.
 *
 * Note this is bookkeeping only — the yaw the camera actually settles towards
 * is accumulated separately (see {@link rotateYawOnEdge}), because deriving it
 * from the index would turn the 3 → 0 step into a −270° spin.
 */
export function snapYawIndex(yawIndex: number, dir: 1 | -1): number {
  return (((yawIndex + dir) % 4) + 4) % 4;
}

/**
 * Apply one frame of Q/E rotation intent to a camera.
 *
 * Edge-triggered: the latches live on the component (not in module state) so
 * two cameras in the same world can't steal each other's key presses, and a
 * held key rotates exactly once.
 *
 * Split out of the system because `isKeyDown` reads DOM-owned input state that
 * only exists once a canvas has focus — this half has to stay testable.
 */
export function rotateYawOnEdge(
  cam: number,
  qDown: boolean,
  eDown: boolean
): void {
  if (IsometricCamera.allowRotate[cam] !== 1) return;

  const step = IsometricCamera.rotateStep[cam] || Math.PI / 2;
  const q = qDown ? 1 : 0;
  const e = eDown ? 1 : 0;

  if (q === 1 && IsometricCamera.qHeld[cam] === 0) {
    IsometricCamera.yawIndex[cam] = snapYawIndex(
      IsometricCamera.yawIndex[cam],
      1
    );
    IsometricCamera.targetYaw[cam] += step;
  }
  if (e === 1 && IsometricCamera.eHeld[cam] === 0) {
    IsometricCamera.yawIndex[cam] = snapYawIndex(
      IsometricCamera.yawIndex[cam],
      -1
    );
    IsometricCamera.targetYaw[cam] -= step;
  }

  IsometricCamera.qHeld[cam] = q;
  IsometricCamera.eHeld[cam] = e;
}

/**
 * Fold a scroll delta into the zoom target.
 *
 * The step is proportional to the current size so one wheel notch feels the
 * same whether you are looking at a whole valley or a single field. Wheel up
 * (negative deltaY) zooms in, matching every orbit control the player has used.
 */
export function applyZoomInput(cam: number, scroll: number): void {
  if (scroll === 0) return;
  const sens = IsometricCamera.zoomSensitivity[cam];
  const size = IsometricCamera.targetOrthoSize[cam];
  const next = size + scroll * sens * size * 0.1;
  IsometricCamera.targetOrthoSize[cam] = Math.min(
    IsometricCamera.maxOrthoSize[cam],
    Math.max(IsometricCamera.minOrthoSize[cam], next)
  );
}

/** Frame-rate-independent low-pass from the current zoom towards its target. */
export function smoothZoom(cam: number, dt: number): void {
  const alpha = 1 - Math.exp(-dt / ZOOM_SMOOTH_TIME);
  IsometricCamera.orthoSize[cam] +=
    (IsometricCamera.targetOrthoSize[cam] - IsometricCamera.orthoSize[cam]) *
    alpha;
}

/**
 * Camera eye position relative to the look-at point.
 *
 * Follows the engine's yaw convention (`processInput` in the player plugin
 * rotates `(0,0,-1)` by `Ry(yaw)`, so a character facing yaw θ heads towards
 * `(−sin θ, 0, −cos θ)`): the camera therefore sits on the opposite side, at
 * `(+sin θ, 0, +cos θ)`, exactly like `ThirdPersonCameraSystem`.
 */
export function isometricEyeOffset(
  yaw: number,
  pitch: number,
  distance: number,
  out: { x: number; y: number; z: number }
): { x: number; y: number; z: number } {
  const cosPitch = Math.cos(pitch);
  out.x = Math.sin(yaw) * distance * cosPitch;
  out.y = Math.sin(pitch) * distance;
  out.z = Math.cos(yaw) * distance * cosPitch;
  return out;
}
