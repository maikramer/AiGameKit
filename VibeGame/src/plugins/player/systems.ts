import { defineQuery, type System } from '../../core';
import { ThirdPersonCamera } from '../player-controller';
import { InputState } from '../input';
import { OrbitCamera } from '../orbit-camera';
import { Rigidbody, CharacterController, CharacterMovement } from '../physics';
import { Transform } from '../transforms';
import { PlayerController } from './components';
import { handleJump, processInput, updateRotation } from './utils';

const playerMovementQuery = defineQuery([
  PlayerController,
  CharacterMovement,
  Transform,
  Rigidbody,
  InputState,
]);
const orbitCameraQuery = defineQuery([OrbitCamera]);
const thirdPersonCameraQuery = defineQuery([ThirdPersonCamera]);
const playerGroundedQuery = defineQuery([
  PlayerController,
  CharacterMovement,
  CharacterController,
  InputState,
  Rigidbody,
]);
const playersQuery = defineQuery([PlayerController]);

/** Camera turn rate (rad/s) when steering the third-person view with A/D. */
const CAMERA_TURN_SPEED = 2.5;
/** How much A/D also moves the character sideways while steering (0..1). */
const SIDE_MOVE_FACTOR = 0.6;

function resolveCameraYaw(world: import('../../core').IWorld): number {
  const orbitCams = orbitCameraQuery(world);
  if (orbitCams.length > 0) return OrbitCamera.currentYaw[orbitCams[0]];
  return 0;
}

export const PlayerMovementSystem: System = {
  group: 'fixed',
  update: (state) => {
    const playerEntities = playerMovementQuery(state.world);

    const thirdPersonCams = thirdPersonCameraQuery(state.world);
    const tpCam = thirdPersonCams.length > 0 ? thirdPersonCams[0] : 0;
    const deltaTime = state.time.fixedDeltaTime;

    for (const entity of playerEntities) {
      // Third-person: A/D steer the camera (and thus the heading) and W/S move
      // along the camera's forward axis. Steering also pushes the character a
      // bit sideways (SIDE_MOVE_FACTOR) so turning carves an arc instead of
      // pivoting in place. Without a third-person camera fall back to an
      // orbit-relative (or world) frame where A/D strafes.
      let cameraYaw: number;
      let strafe: number;
      if (tpCam !== 0) {
        ThirdPersonCamera.yaw[tpCam] -=
          InputState.moveX[entity] * CAMERA_TURN_SPEED * deltaTime;
        cameraYaw = ThirdPersonCamera.yaw[tpCam];
        strafe = InputState.moveX[entity] * SIDE_MOVE_FACTOR;
      } else {
        cameraYaw = resolveCameraYaw(state.world);
        strafe = InputState.moveX[entity];
      }

      const inputVector = processInput(
        InputState.moveY[entity],
        strafe,
        cameraYaw
      );

      // processInput normalizes the direction, so reapply the input magnitude:
      // A/D alone should only nudge the character sideways (SIDE_MOVE_FACTOR),
      // not push it at full speed.
      const inputMag = Math.min(
        1,
        Math.hypot(InputState.moveY[entity], strafe)
      );

      const speed = PlayerController.speed[entity];
      const sprintMult =
        InputState.sprint[entity] === 1
          ? PlayerController.sprintMultiplier[entity]
          : 1;
      const finalSpeed = speed * sprintMult * inputMag;
      const horizontalVelX = inputVector.x * finalSpeed;
      const horizontalVelZ = inputVector.z * finalSpeed;

      const platform = state.hasComponent(entity, CharacterController)
        ? CharacterController.platform[entity]
        : null;
      const jumpVelocity = handleJump(
        entity,
        InputState.jump[entity],
        state.time.elapsed * 1000,
        platform
      );

      if (jumpVelocity > 0 && state.hasComponent(entity, CharacterController)) {
        const currentPlatform = CharacterController.platform[entity];
        if (currentPlatform > 0) {
          PlayerController.inheritedVelX[entity] =
            CharacterController.platformVelX[entity];
          PlayerController.inheritedVelZ[entity] =
            CharacterController.platformVelZ[entity];

          if (state.hasComponent(currentPlatform, Rigidbody)) {
            PlayerController.inheritedAngVelX[entity] =
              Rigidbody.rotVelX[currentPlatform] || 0;
            PlayerController.inheritedAngVelY[entity] =
              Rigidbody.rotVelY[currentPlatform] || 0;
            PlayerController.inheritedAngVelZ[entity] =
              Rigidbody.rotVelZ[currentPlatform] || 0;

            PlayerController.platformOffsetX[entity] =
              Rigidbody.posX[entity] - Rigidbody.posX[currentPlatform];
            PlayerController.platformOffsetY[entity] =
              Rigidbody.posY[entity] - Rigidbody.posY[currentPlatform];
            PlayerController.platformOffsetZ[entity] =
              Rigidbody.posZ[entity] - Rigidbody.posZ[currentPlatform];
          }
        }
      }

      const momentumFactor =
        PlayerController.isJumping[entity] === 1 ? 0.85 : 0;
      CharacterMovement.desiredVelX[entity] =
        horizontalVelX +
        PlayerController.inheritedVelX[entity] * momentumFactor;
      CharacterMovement.desiredVelZ[entity] =
        horizontalVelZ +
        PlayerController.inheritedVelZ[entity] * momentumFactor;

      if (PlayerController.isJumping[entity] === 1) {
        PlayerController.inheritedVelX[entity] *= 0.98;
        PlayerController.inheritedVelZ[entity] *= 0.98;
      }

      if (jumpVelocity > 0) {
        CharacterMovement.velocityY[entity] = jumpVelocity;
      }
      CharacterMovement.desiredVelY[entity] = 0;

      if (PlayerController.jumpCooldown[entity] > 0) {
        PlayerController.jumpCooldown[entity] -= deltaTime;
        if (PlayerController.jumpCooldown[entity] <= 0) {
          PlayerController.jumpCooldown[entity] = 0;
          PlayerController.canJump[entity] = 1;
        }
      }

      const newRotation = updateRotation(
        entity,
        inputVector,
        deltaTime,
        {
          rotX: Rigidbody.rotX[entity],
          rotY: Rigidbody.rotY[entity],
          rotZ: Rigidbody.rotZ[entity],
          rotW: Rigidbody.rotW[entity],
        },
        cameraYaw,
        state.world
      );

      Rigidbody.rotX[entity] = newRotation.x;
      Rigidbody.rotY[entity] = newRotation.y;
      Rigidbody.rotZ[entity] = newRotation.z;
      Rigidbody.rotW[entity] = newRotation.w;
    }
  },
};

export const PlayerGroundedSystem: System = {
  group: 'fixed',
  before: [PlayerMovementSystem],
  update: (state) => {
    const players = playerGroundedQuery(state.world);

    for (const entity of players) {
      const isGrounded = CharacterController.grounded[entity] === 1;
      const wasJumping = PlayerController.isJumping[entity] === 1;
      const currentPlatform = CharacterController.platform[entity];

      if (currentPlatform !== PlayerController.lastPlatform[entity]) {
        PlayerController.lastPlatform[entity] = currentPlatform;

        if (
          currentPlatform > 0 &&
          state.hasComponent(currentPlatform, Rigidbody)
        ) {
          PlayerController.platformOffsetX[entity] =
            Rigidbody.posX[entity] - Rigidbody.posX[currentPlatform];
          PlayerController.platformOffsetY[entity] =
            Rigidbody.posY[entity] - Rigidbody.posY[currentPlatform];
          PlayerController.platformOffsetZ[entity] =
            Rigidbody.posZ[entity] - Rigidbody.posZ[currentPlatform];
        }
      }

      if (isGrounded) {
        PlayerController.lastGroundedTime[entity] = state.time.elapsed * 1000;

        if (wasJumping) {
          PlayerController.isJumping[entity] = 0;
          PlayerController.inheritedVelX[entity] = 0;
          PlayerController.inheritedVelZ[entity] = 0;
          PlayerController.inheritedAngVelX[entity] = 0;
          PlayerController.inheritedAngVelY[entity] = 0;
          PlayerController.inheritedAngVelZ[entity] = 0;
        }

        if (
          PlayerController.canJump[entity] === 0 &&
          PlayerController.jumpCooldown[entity] <= 0
        ) {
          PlayerController.canJump[entity] = 1;
        }
      }
    }
  },
};

export const PlayerCameraLinkingSystem: System = {
  group: 'simulation',
  update: (state) => {
    const players = playersQuery(state.world);

    const thirdPersonCams = thirdPersonCameraQuery(state.world);
    const orbitCams = orbitCameraQuery(state.world);

    for (const player of players) {
      if (PlayerController.cameraEntity[player] !== 0) continue;

      if (thirdPersonCams.length > 0) {
        const cam = thirdPersonCams[0];
        PlayerController.cameraEntity[player] = cam;
        ThirdPersonCamera.target[cam] = player;
      } else if (orbitCams.length > 0) {
        const cam = orbitCams[0];
        PlayerController.cameraEntity[player] = cam;
        OrbitCamera.target[cam] = player;
        OrbitCamera.inputSource[cam] = player;
      }
    }
  },
};
