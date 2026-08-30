import { beforeEach, describe, expect, it } from 'bun:test';
import {
  ISO_PITCH,
  IsometricCamera,
  IsometricCameraPlugin,
  State,
  applyZoomInput,
  isometricEyeOffset,
  isometricCameraRecipe,
  isometricCameraParser,
  rotateYawOnEdge,
  smoothZoom,
  snapYawIndex,
} from 'aigamekit-vibegame';
import { TransformsPlugin } from 'aigamekit-vibegame/transforms';

const TAU = Math.PI * 2;

describe('isometric-camera logic', () => {
  let state: State;
  let cam: number;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(IsometricCameraPlugin);
    cam = state.createEntity();
    state.addComponent(cam, IsometricCamera);
  });

  describe('recipe & defaults', () => {
    it('declares an orthographic main camera', () => {
      expect(isometricCameraRecipe.overrides?.['main-camera.projection']).toBe(
        1
      );
    });

    it('is a merge recipe (must stay top-level, like ThirdPersonCamera)', () => {
      expect(isometricCameraRecipe.merge).toBe(true);
    });

    it('carries transform and main-camera components', () => {
      expect(isometricCameraRecipe.components).toContain('transform');
      expect(isometricCameraRecipe.components).toContain('main-camera');
      expect(isometricCameraRecipe.components).toContain('isometric-camera');
    });

    it('ships sane near/far for a rig standing tens of metres back', () => {
      expect(isometricCameraRecipe.overrides?.['main-camera.near']).toBe(1);
      expect(isometricCameraRecipe.overrides?.['main-camera.far']).toBe(600);
    });

    it('defaults pitch to the true isometric angle', () => {
      expect(IsometricCamera.pitch[cam]).toBeCloseTo(ISO_PITCH, 6);
    });

    it('ISO_PITCH is atan(1/sqrt(2)) ≈ 35.264°', () => {
      expect((ISO_PITCH * 180) / Math.PI).toBeCloseTo(35.264, 3);
    });

    it('defaults yaw to the 45° corner-on framing', () => {
      expect(IsometricCamera.yaw[cam]).toBeCloseTo(Math.PI / 4, 6);
      expect(IsometricCamera.targetYaw[cam]).toBeCloseTo(Math.PI / 4, 6);
    });

    it('defaults rotation to 90° steps, enabled', () => {
      expect(IsometricCamera.rotateStep[cam]).toBeCloseTo(Math.PI / 2, 6);
      expect(IsometricCamera.allowRotate[cam]).toBe(1);
    });

    it('starts uninitialized so the first frame snaps', () => {
      expect(IsometricCamera.initialized[cam]).toBe(0);
    });

    it('defaults zoom bounds around the starting ortho size', () => {
      expect(IsometricCamera.orthoSize[cam]).toBe(22);
      expect(IsometricCamera.minOrthoSize[cam]).toBeLessThan(22);
      expect(IsometricCamera.maxOrthoSize[cam]).toBeGreaterThan(22);
    });
  });

  describe('isometricCameraParser', () => {
    /** Mirror the XML pipeline: plain field attributes land on the component
     *  first, then the parser runs. */
    const parse = (attributes: Record<string, number>) => {
      const e = state.createEntity();
      state.addComponent(e, IsometricCamera);
      if (attributes['ortho-size'] != null) {
        IsometricCamera.orthoSize[e] = attributes['ortho-size']!;
      }
      if (attributes['target-ortho-size'] != null) {
        IsometricCamera.targetOrthoSize[e] = attributes['target-ortho-size']!;
      }
      isometricCameraParser({
        entity: e,
        element: { tagName: 'IsometricCamera', attributes, children: [] },
        state,
      } as never);
      return e;
    };

    it('seeds the zoom target from the authored ortho-size', () => {
      const e = parse({ 'ortho-size': 16 });
      // Without the seed, the low-pass drags the framing back to the default
      // 22 within a second of boot.
      expect(IsometricCamera.orthoSize[e]).toBe(16);
      expect(IsometricCamera.targetOrthoSize[e]).toBe(16);
    });

    it('leaves an explicit target alone', () => {
      const e = parse({ 'ortho-size': 16, 'target-ortho-size': 30 });
      expect(IsometricCamera.orthoSize[e]).toBe(16);
      expect(IsometricCamera.targetOrthoSize[e]).toBe(30);
    });

    it('does nothing when no ortho-size is authored', () => {
      const e = parse({ distance: 90 });
      expect(IsometricCamera.targetOrthoSize[e]).toBe(22);
    });
  });

  describe('snapYawIndex', () => {
    it('cycles forward 0→1→2→3→0', () => {
      expect(snapYawIndex(0, 1)).toBe(1);
      expect(snapYawIndex(1, 1)).toBe(2);
      expect(snapYawIndex(2, 1)).toBe(3);
      expect(snapYawIndex(3, 1)).toBe(0);
    });

    it('cycles backward 0→3→2→1→0', () => {
      expect(snapYawIndex(0, -1)).toBe(3);
      expect(snapYawIndex(3, -1)).toBe(2);
      expect(snapYawIndex(2, -1)).toBe(1);
      expect(snapYawIndex(1, -1)).toBe(0);
    });

    it('never returns a negative index', () => {
      let i = 0;
      for (let n = 0; n < 12; n++) {
        i = snapYawIndex(i, -1);
        expect(i).toBeGreaterThanOrEqual(0);
        expect(i).toBeLessThan(4);
      }
    });
  });

  describe('rotateYawOnEdge', () => {
    it('rotates once per press, not once per frame', () => {
      const start = IsometricCamera.targetYaw[cam];
      rotateYawOnEdge(cam, true, false);
      rotateYawOnEdge(cam, true, false);
      rotateYawOnEdge(cam, true, false);
      expect(IsometricCamera.targetYaw[cam] - start).toBeCloseTo(
        Math.PI / 2,
        6
      );
    });

    it('rotates again after the key is released', () => {
      const start = IsometricCamera.targetYaw[cam];
      rotateYawOnEdge(cam, true, false);
      rotateYawOnEdge(cam, false, false);
      rotateYawOnEdge(cam, true, false);
      expect(IsometricCamera.targetYaw[cam] - start).toBeCloseTo(Math.PI, 6);
    });

    it('E rotates the opposite way from Q', () => {
      const start = IsometricCamera.targetYaw[cam];
      rotateYawOnEdge(cam, false, true);
      expect(IsometricCamera.targetYaw[cam] - start).toBeCloseTo(
        -Math.PI / 2,
        6
      );
    });

    it('advances the quadrant index alongside the yaw', () => {
      rotateYawOnEdge(cam, true, false);
      expect(IsometricCamera.yawIndex[cam]).toBe(1);
      rotateYawOnEdge(cam, false, false);
      rotateYawOnEdge(cam, false, true);
      expect(IsometricCamera.yawIndex[cam]).toBe(0);
    });

    it('accumulates past ±π instead of wrapping — this is what keeps every step a 90° short path', () => {
      const start = IsometricCamera.targetYaw[cam];
      for (let n = 0; n < 6; n++) {
        rotateYawOnEdge(cam, true, false);
        rotateYawOnEdge(cam, false, false);
      }
      const delta = IsometricCamera.targetYaw[cam] - start;
      expect(delta).toBeCloseTo(6 * (Math.PI / 2), 5);
      // A wrapped implementation would have folded this back inside [-π, π].
      expect(Math.abs(delta)).toBeGreaterThan(Math.PI);
    });

    it('four full steps return to the same heading modulo 2π', () => {
      const start = IsometricCamera.targetYaw[cam];
      for (let n = 0; n < 4; n++) {
        rotateYawOnEdge(cam, true, false);
        rotateYawOnEdge(cam, false, false);
      }
      expect(IsometricCamera.targetYaw[cam] - start).toBeCloseTo(TAU, 5);
      expect(IsometricCamera.yawIndex[cam]).toBe(0);
    });

    it('does nothing when allow-rotate is 0', () => {
      IsometricCamera.allowRotate[cam] = 0;
      const start = IsometricCamera.targetYaw[cam];
      rotateYawOnEdge(cam, true, false);
      expect(IsometricCamera.targetYaw[cam]).toBe(start);
      expect(IsometricCamera.yawIndex[cam]).toBe(0);
    });

    it('honours a custom rotate step', () => {
      IsometricCamera.rotateStep[cam] = Math.PI / 4;
      const start = IsometricCamera.targetYaw[cam];
      rotateYawOnEdge(cam, true, false);
      expect(IsometricCamera.targetYaw[cam] - start).toBeCloseTo(
        Math.PI / 4,
        6
      );
    });

    it('keeps per-camera latches independent', () => {
      const other = state.createEntity();
      state.addComponent(other, IsometricCamera);
      rotateYawOnEdge(cam, true, false);
      expect(IsometricCamera.qHeld[cam]).toBe(1);
      expect(IsometricCamera.qHeld[other]).toBe(0);
    });

    it('applies Q and E in the same frame as a net zero', () => {
      const start = IsometricCamera.targetYaw[cam];
      rotateYawOnEdge(cam, true, true);
      expect(IsometricCamera.targetYaw[cam]).toBeCloseTo(start, 6);
      expect(IsometricCamera.yawIndex[cam]).toBe(0);
    });
  });

  describe('zoom', () => {
    it('wheel up (negative delta) zooms in — shrinks the frustum', () => {
      applyZoomInput(cam, -1);
      expect(IsometricCamera.targetOrthoSize[cam]).toBeLessThan(22);
    });

    it('wheel down (positive delta) zooms out — grows it', () => {
      applyZoomInput(cam, 1);
      expect(IsometricCamera.targetOrthoSize[cam]).toBeGreaterThan(22);
    });

    it('clamps at the minimum', () => {
      for (let n = 0; n < 200; n++) applyZoomInput(cam, -1);
      expect(IsometricCamera.targetOrthoSize[cam]).toBeCloseTo(
        IsometricCamera.minOrthoSize[cam],
        5
      );
    });

    it('clamps at the maximum', () => {
      for (let n = 0; n < 200; n++) applyZoomInput(cam, 1);
      expect(IsometricCamera.targetOrthoSize[cam]).toBeCloseTo(
        IsometricCamera.maxOrthoSize[cam],
        5
      );
    });

    it('is a no-op for zero scroll', () => {
      applyZoomInput(cam, 0);
      expect(IsometricCamera.targetOrthoSize[cam]).toBe(22);
    });

    it('steps proportionally, so a notch feels the same at every zoom', () => {
      applyZoomInput(cam, -1);
      const firstStep = 22 - IsometricCamera.targetOrthoSize[cam];
      const before = IsometricCamera.targetOrthoSize[cam];
      applyZoomInput(cam, -1);
      const secondStep = before - IsometricCamera.targetOrthoSize[cam];
      expect(secondStep).toBeLessThan(firstStep);
      expect(secondStep / before).toBeCloseTo(firstStep / 22, 6);
    });

    it('converges towards the target without overshooting', () => {
      IsometricCamera.targetOrthoSize[cam] = 30;
      for (let n = 0; n < 200; n++) smoothZoom(cam, 1 / 60);
      expect(IsometricCamera.orthoSize[cam]).toBeCloseTo(30, 4);
    });

    it('moves less in a shorter frame', () => {
      IsometricCamera.targetOrthoSize[cam] = 40;
      smoothZoom(cam, 1 / 240);
      const small = IsometricCamera.orthoSize[cam];
      IsometricCamera.orthoSize[cam] = 22;
      smoothZoom(cam, 1 / 30);
      expect(IsometricCamera.orthoSize[cam]).toBeGreaterThan(small);
    });
  });

  describe('isometricEyeOffset', () => {
    const out = { x: 0, y: 0, z: 0 };

    it('preserves the arm length', () => {
      isometricEyeOffset(1.1, ISO_PITCH, 70, out);
      expect(Math.hypot(out.x, out.y, out.z)).toBeCloseTo(70, 5);
    });

    it('always looks down (positive Y offset for a positive pitch)', () => {
      for (const yaw of [0, 1, 2, 3, 4, 5, 6]) {
        isometricEyeOffset(yaw, ISO_PITCH, 70, out);
        expect(out.y).toBeGreaterThan(0);
      }
    });

    it('sits opposite the character forward direction', () => {
      // processInput rotates (0,0,-1) by Ry(yaw): forward = (-sin, 0, -cos).
      const yaw = 0.7;
      isometricEyeOffset(yaw, ISO_PITCH, 10, out);
      const fx = -Math.sin(yaw);
      const fz = -Math.cos(yaw);
      const horiz = Math.hypot(out.x, out.z);
      expect(out.x / horiz).toBeCloseTo(-fx, 6);
      expect(out.z / horiz).toBeCloseTo(-fz, 6);
    });

    it('is behind on +Z for yaw 0', () => {
      isometricEyeOffset(0, 0, 10, out);
      expect(out.x).toBeCloseTo(0, 6);
      expect(out.z).toBeCloseTo(10, 6);
    });

    it('is on +X for yaw π/2', () => {
      isometricEyeOffset(Math.PI / 2, 0, 10, out);
      expect(out.x).toBeCloseTo(10, 6);
      expect(out.z).toBeCloseTo(0, 6);
    });

    it('is directly overhead at pitch π/2', () => {
      isometricEyeOffset(1.234, Math.PI / 2, 10, out);
      expect(out.x).toBeCloseTo(0, 6);
      expect(out.y).toBeCloseTo(10, 6);
      expect(out.z).toBeCloseTo(0, 6);
    });

    it('repeats every 2π of yaw', () => {
      const a = { x: 0, y: 0, z: 0 };
      const b = { x: 0, y: 0, z: 0 };
      isometricEyeOffset(0.4, ISO_PITCH, 70, a);
      isometricEyeOffset(0.4 + TAU, ISO_PITCH, 70, b);
      expect(b.x).toBeCloseTo(a.x, 5);
      expect(b.z).toBeCloseTo(a.z, 5);
    });

    it('returns the same object it was given', () => {
      expect(isometricEyeOffset(0, 0, 1, out)).toBe(out);
    });
  });
});
