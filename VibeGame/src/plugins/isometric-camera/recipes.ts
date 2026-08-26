import type { Recipe } from '../../core';

/**
 * `<IsometricCamera>` — orthographic fixed-angle camera.
 *
 * The `main-camera.*` overrides are applied when the entity is created, which
 * matters: `createThreeCamera` picks `THREE.OrthographicCamera` vs
 * `PerspectiveCamera` once, at construction, and `syncCameraSettings` cannot
 * switch the projection type afterwards. Declaring the projection here is what
 * makes the camera come out orthographic without any runtime toggle.
 *
 * `merge: true` — like `<ThirdPersonCamera>` and `<OrbitCamera>`, this must
 * stay a top-level element in the scene, never nested inside a `<Group>`.
 */
export const isometricCameraRecipe: Recipe = {
  name: 'IsometricCamera',
  components: ['isometric-camera', 'transform', 'main-camera'],
  merge: true,
  overrides: {
    'main-camera.projection': 1, // CameraProjection.ORTHOGRAPHIC
    'main-camera.near': 1,
    'main-camera.far': 600,
    'main-camera.ortho-size': 22,
  },
};
