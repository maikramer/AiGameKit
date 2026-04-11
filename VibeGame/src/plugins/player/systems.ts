import { defineQuery, type System } from '../../core';
import { FollowCamera } from '../follow-camera';
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
const followCameraQuery = defineQuery([FollowCamera]);
const playerGroundedQuery = defineQuery([
  PlayerController,
  CharacterMovement,
  CharacterController,
  InputState,
  Rigidbody,
]);
const playersQuery = defineQuery([PlayerController]);

function resolveCameraYaw(world: import('bitecs').IWorld): number {
  const followCams = followCameraQuery(world);
  if (followCams.length > 0) return FollowCamera.currentYaw[followCams[0]];
  const orbitCams = orbitCameraQuery(world);
  if (orbitCams.length > 0) return OrbitCamera.currentYaw[orbitCams[0]];
  return 0;
}

export const PlayerMovementSystem: System = {
  group: 'fixed',
  update: (state) => {
    const playerEntities = playerMovementQuery(state.world);

    const cameraYaw = resolveCameraYaw(state.world);
    const deltaTime = state.time.fixedDeltaTime;

    for (const entity of playerEntities) {
      const inputVector = processInput(
        InputState.moveY[entity],
        InputState.moveX[entity],
        cameraYaw
      );

      const speed = PlayerController.speed[entity];
      const horizontalVelX = inputVector.x * speed;
      const horizontalVelZ = inputVector.z * speed;

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

      const newRotation = updateRotation(entity, inputVector, deltaTime, {
        rotX: Rigidbody.rotX[entity],
        rotY: Rigidbody.rotY[entity],
        rotZ: Rigidbody.rotZ[entity],
        rotW: Rigidbody.rotW[entity],
      });

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

    const followCams = followCameraQuery(state.world);
    const orbitCams = orbitCameraQuery(state.world);

    for (const player of players) {
      if (PlayerController.cameraEntity[player] !== 0) continue;

      if (followCams.length > 0) {
        const cam = followCams[0];
        PlayerController.cameraEntity[player] = cam;
        FollowCamera.target[cam] = player;
        FollowCamera.inputSource[cam] = player;
      } else if (orbitCams.length > 0) {
        const cam = orbitCams[0];
        PlayerController.cameraEntity[player] = cam;
        OrbitCamera.target[cam] = player;
        OrbitCamera.inputSource[cam] = player;
      }
    }
  },
};
