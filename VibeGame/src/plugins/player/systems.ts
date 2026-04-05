import { defineQuery, type System } from '../../core';
import { FollowCamera } from '../follow-camera';
import { InputState } from '../input';
import { OrbitCamera } from '../orbit-camera';
import { Body, CharacterController, CharacterMovement } from '../physics';
import { Transform } from '../transforms';
import { Player } from './components';
import { handleJump, processInput, updateRotation } from './utils';

const playerMovementQuery = defineQuery([
  Player,
  CharacterMovement,
  Transform,
  Body,
  InputState,
]);
const orbitCameraQuery = defineQuery([OrbitCamera]);
const followCameraQuery = defineQuery([FollowCamera]);
const playerGroundedQuery = defineQuery([
  Player,
  CharacterMovement,
  CharacterController,
  InputState,
  Body,
]);
const playersQuery = defineQuery([Player]);

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

      const speed = Player.speed[entity];
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
          Player.inheritedVelX[entity] =
            CharacterController.platformVelX[entity];
          Player.inheritedVelZ[entity] =
            CharacterController.platformVelZ[entity];

          if (state.hasComponent(currentPlatform, Body)) {
            Player.inheritedAngVelX[entity] =
              Body.rotVelX[currentPlatform] || 0;
            Player.inheritedAngVelY[entity] =
              Body.rotVelY[currentPlatform] || 0;
            Player.inheritedAngVelZ[entity] =
              Body.rotVelZ[currentPlatform] || 0;

            Player.platformOffsetX[entity] =
              Body.posX[entity] - Body.posX[currentPlatform];
            Player.platformOffsetY[entity] =
              Body.posY[entity] - Body.posY[currentPlatform];
            Player.platformOffsetZ[entity] =
              Body.posZ[entity] - Body.posZ[currentPlatform];
          }
        }
      }

      const momentumFactor = Player.isJumping[entity] === 1 ? 0.85 : 0;
      CharacterMovement.desiredVelX[entity] =
        horizontalVelX + Player.inheritedVelX[entity] * momentumFactor;
      CharacterMovement.desiredVelZ[entity] =
        horizontalVelZ + Player.inheritedVelZ[entity] * momentumFactor;

      if (Player.isJumping[entity] === 1) {
        Player.inheritedVelX[entity] *= 0.98;
        Player.inheritedVelZ[entity] *= 0.98;
      }

      if (jumpVelocity > 0) {
        CharacterMovement.velocityY[entity] = jumpVelocity;
      }
      CharacterMovement.desiredVelY[entity] = 0;

      if (Player.jumpCooldown[entity] > 0) {
        Player.jumpCooldown[entity] -= deltaTime;
        if (Player.jumpCooldown[entity] <= 0) {
          Player.jumpCooldown[entity] = 0;
          Player.canJump[entity] = 1;
        }
      }

      const newRotation = updateRotation(entity, inputVector, deltaTime, {
        rotX: Body.rotX[entity],
        rotY: Body.rotY[entity],
        rotZ: Body.rotZ[entity],
        rotW: Body.rotW[entity],
      });

      Body.rotX[entity] = newRotation.x;
      Body.rotY[entity] = newRotation.y;
      Body.rotZ[entity] = newRotation.z;
      Body.rotW[entity] = newRotation.w;
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
      const wasJumping = Player.isJumping[entity] === 1;
      const currentPlatform = CharacterController.platform[entity];

      if (currentPlatform !== Player.lastPlatform[entity]) {
        Player.lastPlatform[entity] = currentPlatform;

        if (currentPlatform > 0 && state.hasComponent(currentPlatform, Body)) {
          Player.platformOffsetX[entity] =
            Body.posX[entity] - Body.posX[currentPlatform];
          Player.platformOffsetY[entity] =
            Body.posY[entity] - Body.posY[currentPlatform];
          Player.platformOffsetZ[entity] =
            Body.posZ[entity] - Body.posZ[currentPlatform];
        }
      }

      if (isGrounded) {
        Player.lastGroundedTime[entity] = state.time.elapsed * 1000;

        if (wasJumping) {
          Player.isJumping[entity] = 0;
          Player.inheritedVelX[entity] = 0;
          Player.inheritedVelZ[entity] = 0;
          Player.inheritedAngVelX[entity] = 0;
          Player.inheritedAngVelY[entity] = 0;
          Player.inheritedAngVelZ[entity] = 0;
        }

        if (Player.canJump[entity] === 0 && Player.jumpCooldown[entity] <= 0) {
          Player.canJump[entity] = 1;
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
      if (Player.cameraEntity[player] !== 0) continue;

      if (followCams.length > 0) {
        const cam = followCams[0];
        Player.cameraEntity[player] = cam;
        FollowCamera.target[cam] = player;
        FollowCamera.inputSource[cam] = player;
      } else if (orbitCams.length > 0) {
        const cam = orbitCams[0];
        Player.cameraEntity[player] = cam;
        OrbitCamera.target[cam] = player;
        OrbitCamera.inputSource[cam] = player;
      }
    }
  },
};
