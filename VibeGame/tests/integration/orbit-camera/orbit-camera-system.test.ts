import { State, TIME_CONSTANTS } from 'vibegame';
import { OrbitCamera, OrbitCameraPlugin } from 'vibegame/orbit-camera';
import {
  Transform,
  TransformsPlugin,
  WorldTransform,
} from 'vibegame/transforms';
import { threeCameras } from 'vibegame/rendering';
import { beforeEach, describe, expect, it } from 'bun:test';
import * as THREE from 'three';

/**
 * OrbitCamera system integration tests.
 *
 * The orbit-camera plugin was migrated to the `camera-controls` npm package
 * (programmatic mode). `OrbitCameraSystem` now:
 *   1. skips an entity when its target has no WorldTransform,
 *   2. skips when `threeCameras.get(entity)` returns no THREE.Camera
 *      (rendering plugin hasn't built it yet),
 *   3. skips when `DOMRect` is undefined (bun test / SSR), because the
 *      camera-controls constructor references DOMRect unconditionally.
 *
 * That means the actual camera *movement* can only be exercised in a real
 * browser (DOMRect + a live WebGL camera-controls instance) — those scenarios
 * are marked `.skip` below and belong in playwright e2e. What we *can* unit-test
 * here is the graceful-skip / setup behaviour, which is what the passing tests
 * cover.
 */
describe('OrbitCamera System Integration', () => {
  let state: State;

  beforeEach(() => {
    // threeCameras is a process-wide singleton map. This suite doesn't load the
    // RenderingPlugin, so it should be empty — clear it to keep tests isolated
    // from any registration another test file may have left behind.
    threeCameras.clear();

    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(OrbitCameraPlugin);
  });

  describe('Graceful handling (non-browser / no-camera skips)', () => {
    it('should not throw or move the camera when no THREE.Camera is registered', () => {
      const targetEntity = state.createEntity();
      const cameraEntity = state.createEntity();

      state.addComponent(targetEntity, WorldTransform);
      state.addComponent(cameraEntity, OrbitCamera);
      state.addComponent(cameraEntity, Transform);

      OrbitCamera.target[cameraEntity] = targetEntity;
      const initialX = Transform.posX[cameraEntity];
      const initialY = Transform.posY[cameraEntity];
      const initialZ = Transform.posZ[cameraEntity];

      // No THREE.Camera in threeCameras → OrbitCameraSystem `continue`s for
      // this entity. It must not throw and must not write the Transform.
      expect(() => state.step(TIME_CONSTANTS.FIXED_TIMESTEP)).not.toThrow();

      expect(Transform.posX[cameraEntity]).toBe(initialX);
      expect(Transform.posY[cameraEntity]).toBe(initialY);
      expect(Transform.posZ[cameraEntity]).toBe(initialZ);
    });

    it('should skip camera-controls construction when DOMRect is undefined (bun test / SSR)', () => {
      const targetEntity = state.createEntity();
      const cameraEntity = state.createEntity();

      state.addComponent(targetEntity, WorldTransform);
      state.addComponent(cameraEntity, OrbitCamera);
      state.addComponent(cameraEntity, Transform);

      OrbitCamera.target[cameraEntity] = targetEntity;
      // Simulate the rendering plugin having built the THREE.Camera for this
      // entity — so the only remaining skip guard is the DOMRect check.
      threeCameras.set(
        cameraEntity,
        new THREE.PerspectiveCamera(75, 16 / 9, 0.1, 1000)
      );

      const initialX = Transform.posX[cameraEntity];
      const initialY = Transform.posY[cameraEntity];
      const initialZ = Transform.posZ[cameraEntity];

      // camera-controls' constructor references DOMRect unconditionally; bun
      // test has no DOMRect, so the system must skip construction rather than
      // throw. The Transform stays untouched.
      expect(() => state.step(TIME_CONSTANTS.FIXED_TIMESTEP)).not.toThrow();

      expect(Transform.posX[cameraEntity]).toBe(initialX);
      expect(Transform.posY[cameraEntity]).toBe(initialY);
      expect(Transform.posZ[cameraEntity]).toBe(initialZ);
    });

    it('should handle missing target gracefully', () => {
      const cameraEntity = state.createEntity();
      state.addComponent(cameraEntity, OrbitCamera);
      state.addComponent(cameraEntity, Transform);

      OrbitCamera.target[cameraEntity] = 999;
      const initialPosX = Transform.posX[cameraEntity];
      const initialPosY = Transform.posY[cameraEntity];
      const initialPosZ = Transform.posZ[cameraEntity];

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Transform.posX[cameraEntity]).toBe(initialPosX);
      expect(Transform.posY[cameraEntity]).toBe(initialPosY);
      expect(Transform.posZ[cameraEntity]).toBe(initialPosZ);
    });

    it('should handle zero target entity', () => {
      const cameraEntity = state.createEntity();
      state.addComponent(cameraEntity, OrbitCamera);
      state.addComponent(cameraEntity, Transform);

      OrbitCamera.target[cameraEntity] = 0;
      const initialPosX = Transform.posX[cameraEntity];

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(Transform.posX[cameraEntity]).toBe(initialPosX);
    });

    it('should handle target without WorldTransform', () => {
      const targetEntity = state.createEntity();
      const cameraEntity = state.createEntity();

      state.addComponent(cameraEntity, OrbitCamera);
      state.addComponent(cameraEntity, Transform);

      OrbitCamera.target[cameraEntity] = targetEntity;
      const initialPosX = Transform.posX[cameraEntity];

      state.step();

      expect(Transform.posX[cameraEntity]).toBe(initialPosX);
    });

    it('should skip cameras without Transform', () => {
      const targetEntity = state.createEntity();
      const cameraEntity = state.createEntity();

      state.addComponent(targetEntity, WorldTransform);
      state.addComponent(cameraEntity, OrbitCamera);
      // Note: no Transform on the camera → excluded from the orbit query.

      OrbitCamera.target[cameraEntity] = targetEntity;

      expect(() => {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }).not.toThrow();
    });
  });

  /**
   * The tests below verify that OrbitCameraSystem actually *moves* the camera.
   * They require:
   *   - a real DOMRect (so the camera-controls constructor runs), and
   *   - a live THREE.Camera + camera-controls instance driving the pose.
   *
   * Neither is available under bun test (DOMRect is undefined here, and there
   * is no WebGL surface). They are kept as `.skip` placeholders so the
   * expected behaviours stay documented; they should be covered by playwright
   * e2e tests running in a real browser.
   */
  describe.skip('Camera movement (browser-only: DOMRect + WebGL camera-controls)', () => {
    it('should update camera Transform when the target moves', () => {
      // Verifies OrbitCameraSystem writes the camera-controls pose back into
      // Transform after the orbit target's WorldTransform changes.
      // Requires: browser env (DOMRect) + a THREE.Camera registered in
      // threeCameras. See playwright e2e.
    });

    it('should support multiple orbit cameras tracking different targets', () => {
      // Two OrbitCamera entities should resolve to distinct camera poses.
      // Requires a browser — see playwright e2e.
    });

    it('should smooth towards target values over time', () => {
      // camera-controls interpolates pose over frames; currentYaw/pitch/distance
      // read back from controls should approach the configured targets. The old
      // targetYaw/targetDistance smoothing is now owned by camera-controls.
      // Requires a browser — see playwright e2e.
    });

    it('should dynamically switch camera target', () => {
      // Reassigning OrbitCamera.target and stepping should move the camera to
      // the new target's pose. Requires a browser — see playwright e2e.
    });

    it('should apply offset values correctly', () => {
      // OrbitCamera.offset* should shift the camera-controls target away from
      // the entity world position. Requires a browser — see playwright e2e.
    });

    it('should rotate and zoom via OrbitCameraInputSystem (programmatic control)', () => {
      // The input system reads InputState (lookX/lookY/scrollDelta/rightMouse)
      // and calls camera-controls rotate()/dolly(). This cannot be exercised
      // without a DOMRect + camera-controls instance. The previous unit tests
      // manually computed `targetYaw += delta * speed` and asserted on the
      // value they just set — vacuous, since the system never wrote it.
      // Requires a browser — see playwright e2e.
    });
  });
});
