import {
  defineSystem,
  defineQuery,
  type Parser,
  type System,
} from '../../core';
import { InputState, isKeyDown } from '../input';
import { MainCamera, threeCameras } from '../rendering';
import { CameraSyncSystem } from '../rendering/systems';
import { setShadowFocusEntity } from '../rendering/shadow-focus';
import {
  Transform,
  WorldTransform,
  syncEulerFromQuaternion,
} from '../transforms';
import { shortestAngleDelta } from '../transforms/utils';
import { IsometricCamera } from './components';
import {
  applyZoomInput,
  isometricEyeOffset,
  rotateYawOnEdge,
  smoothZoom,
} from './logic';

const isoCameraQuery = defineQuery([IsometricCamera, Transform]);
const isoCameraInputQuery = defineQuery([IsometricCamera]);
const inputStateQuery = defineQuery([InputState]);

/** Scratch for the eye offset — this system is not reentrant. */
const _eye = { x: 0, y: 0, z: 0 };

/**
 * Parses `<IsometricCamera>`: the authored `ortho-size` must seed BOTH the
 * smoothed value (normal attribute routing) and its target. Left at the default,
 * the target drags the framing away from the authored size within a second of
 * boot — the same authored/derived split `TerrainPad` makes between `height`
 * and `heightMode`.
 */
export const isometricCameraParser: Parser = ({ entity, element }) => {
  if (
    element.attributes['ortho-size'] != null &&
    element.attributes['target-ortho-size'] == null
  ) {
    IsometricCamera.targetOrthoSize[entity] = IsometricCamera.orthoSize[entity];
  }
};

/**
 * Q/E quadrant rotation and scroll zoom.
 *
 * Runs in `simulation` on purpose: it writes `MainCamera.orthoSize`, which
 * `CameraSyncSystem` (draw) reads to rebuild the orthographic frustum. Writing
 * it from a system ordered after `CameraSyncSystem` would apply the zoom one
 * frame late and make the wheel feel spongy.
 */
export const IsometricCameraInputSystem: System = defineSystem({
  name: 'IsometricCameraInputSystem',
  group: 'simulation',
  update: (state) => {
    const dt = state.time.deltaTime;

    for (const cam of isoCameraInputQuery(state.world)) {
      // Resolve an input source once: the player's InputState if the linking
      // system already bound one, else the first InputState in the world.
      let inputSource = IsometricCamera.inputSource[cam];
      if (!inputSource || !state.hasComponent(inputSource, InputState)) {
        const sources = inputStateQuery(state.world);
        inputSource = sources.length > 0 ? sources[0] : 0;
        IsometricCamera.inputSource[cam] = inputSource;
      }

      rotateYawOnEdge(cam, isKeyDown('KeyQ'), isKeyDown('KeyE'));

      if (inputSource && state.hasComponent(inputSource, InputState)) {
        applyZoomInput(cam, InputState.scrollDelta[inputSource]);
      }
      smoothZoom(cam, dt);

      if (state.hasComponent(cam, MainCamera)) {
        MainCamera.orthoSize[cam] = IsometricCamera.orthoSize[cam];
      }
    }
  },
});

/**
 * Pose the camera: smoothed follow point, smoothed yaw, fixed pitch.
 *
 * `after: [CameraSyncSystem]` for the same reason as `ThirdPersonCameraSystem`
 * — this system must be the sole authority over the THREE camera transform,
 * otherwise the two fight and the view jitters every frame.
 */
export const IsometricCameraSystem: System = defineSystem({
  name: 'IsometricCameraSystem',
  group: 'draw',
  after: [CameraSyncSystem],
  update: (state) => {
    const dt = state.time.deltaTime;

    for (const cam of isoCameraQuery(state.world)) {
      const target = IsometricCamera.target[cam];
      if (!target || !state.hasComponent(target, WorldTransform)) continue;

      // The directional light's shadow box follows this entity instead of the
      // camera: an orthographic rig stands tens of metres back, far enough that
      // the camera-centred fallback would leave the hero outside the box.
      setShadowFocusEntity(state, target);

      const rawX = WorldTransform.posX[target];
      const rawY = WorldTransform.posY[target];
      const rawZ = WorldTransform.posZ[target];

      if (IsometricCamera.initialized[cam] === 0) {
        // First frame snaps, so the view never swoops in from the origin.
        IsometricCamera.followX[cam] = rawX;
        IsometricCamera.followY[cam] = rawY;
        IsometricCamera.followZ[cam] = rawZ;
        IsometricCamera.yaw[cam] = IsometricCamera.targetYaw[cam];
        IsometricCamera.initialized[cam] = 1;
      } else {
        const followLag = Math.max(1e-4, IsometricCamera.followLag[cam]);
        const followLagY = Math.max(1e-4, IsometricCamera.followLagY[cam]);
        const aXZ = 1 - Math.exp(-dt / followLag);
        const aY = 1 - Math.exp(-dt / followLagY);
        IsometricCamera.followX[cam] +=
          (rawX - IsometricCamera.followX[cam]) * aXZ;
        IsometricCamera.followY[cam] +=
          (rawY - IsometricCamera.followY[cam]) * aY;
        IsometricCamera.followZ[cam] +=
          (rawZ - IsometricCamera.followZ[cam]) * aXZ;

        // Shortest angular path to the quadrant target, so a 3 → 0 step sweeps
        // 90° and not 270°.
        const turnLag = Math.max(1e-4, IsometricCamera.turnLag[cam]);
        const aYaw = 1 - Math.exp(-dt / turnLag);
        IsometricCamera.yaw[cam] +=
          shortestAngleDelta(
            IsometricCamera.yaw[cam],
            IsometricCamera.targetYaw[cam]
          ) * aYaw;
      }

      const lookX = IsometricCamera.followX[cam] + IsometricCamera.offsetX[cam];
      const lookY = IsometricCamera.followY[cam] + IsometricCamera.offsetY[cam];
      const lookZ = IsometricCamera.followZ[cam] + IsometricCamera.offsetZ[cam];

      isometricEyeOffset(
        IsometricCamera.yaw[cam],
        IsometricCamera.pitch[cam],
        IsometricCamera.distance[cam],
        _eye
      );
      const eyeX = lookX + _eye.x;
      const eyeY = lookY + _eye.y;
      const eyeZ = lookZ + _eye.z;

      Transform.posX[cam] = eyeX;
      Transform.posY[cam] = eyeY;
      Transform.posZ[cam] = eyeZ;

      // Look-at quaternion: shortest arc taking the camera's local −Z onto the
      // view direction. q = (fy, −fx, 0, 1 − fz) — see ThirdPersonCameraSystem
      // for why the naive (f×up, 1 + f·up) form is wrong here.
      const dx = lookX - eyeX;
      const dy = lookY - eyeY;
      const dz = lookZ - eyeZ;
      const len = Math.sqrt(dx * dx + dy * dy + dz * dz);
      if (len > 0.001) {
        const fx = dx / len;
        const fy = dy / len;
        const fz = dz / len;
        const rx = fy;
        const ry = -fx;
        const rz = 0;
        const rw = 1 - fz;
        const mag = Math.sqrt(rx * rx + ry * ry + rz * rz + rw * rw);
        if (mag > 0.001) {
          Transform.rotX[cam] = rx / mag;
          Transform.rotY[cam] = ry / mag;
          Transform.rotZ[cam] = rz / mag;
          Transform.rotW[cam] = rw / mag;
        }
      }

      syncEulerFromQuaternion(Transform, cam);
      Transform.dirty[cam] = 1;

      // Mirror onto the THREE camera so the pose lands this frame rather than
      // waiting for the next CameraSyncSystem pass.
      const threeCamera = threeCameras.get(cam);
      if (threeCamera) {
        threeCamera.position.set(eyeX, eyeY, eyeZ);
        threeCamera.quaternion.set(
          Transform.rotX[cam],
          Transform.rotY[cam],
          Transform.rotZ[cam],
          Transform.rotW[cam]
        );
        threeCamera.updateMatrixWorld();
      }
    }
  },
});
