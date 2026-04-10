import { INPUT_CONFIG } from '../input';
import {
  Body,
  CharacterController,
  CharacterMovement,
  DEFAULT_GRAVITY,
} from '../physics';
import { Player } from './components';
import * as THREE from 'three';

const _tmpVec3 = new THREE.Vector3();
const _tmpMat4 = new THREE.Matrix4();
const _tmpQuat = new THREE.Quaternion();
const _tmpQuat2 = new THREE.Quaternion();
const _tmpEuler = new THREE.Euler();

export const JUMP_CONSTANTS = {
  verticalVelocityThreshold: 0.001,
  cooldown: 0.2,
};

export function calculateTangentialVelocity(
  angVelX: number,
  angVelY: number,
  angVelZ: number,
  offsetX: number,
  offsetY: number,
  offsetZ: number
): THREE.Vector3 {
  const tangentialVelX = angVelY * offsetZ - angVelZ * offsetY;
  const tangentialVelY = angVelZ * offsetX - angVelX * offsetZ;
  const tangentialVelZ = angVelX * offsetY - angVelY * offsetX;
  return _tmpVec3.set(tangentialVelX, tangentialVelY, tangentialVelZ);
}

export function processInput(
  moveForward: number,
  moveRight: number,
  cameraYaw: number
): THREE.Vector3 {
  _tmpVec3.set(moveRight, 0, -moveForward);
  if (_tmpVec3.length() > 0) {
    _tmpVec3.normalize();
    _tmpMat4.makeRotationY(cameraYaw);
    _tmpVec3.applyMatrix4(_tmpMat4);
  }
  return _tmpVec3;
}

function canPerformJump(entity: number, currentTime: number): boolean {
  const timeSinceGrounded = currentTime - Player.lastGroundedTime[entity];
  const timeSinceJumpPressed = currentTime - Player.jumpBufferTime[entity];
  const isGrounded = CharacterController.grounded[entity] === 1;

  return (
    timeSinceJumpPressed <= INPUT_CONFIG.bufferWindow &&
    Player.canJump[entity] === 1 &&
    (isGrounded || timeSinceGrounded <= INPUT_CONFIG.gracePeriods.coyoteTime)
  );
}

function calculateJumpVelocity(entity: number): number {
  const gravityScale = Body.gravityScale[entity];
  const gravity = Math.abs(DEFAULT_GRAVITY * gravityScale);
  return Math.sqrt(2 * gravity * Player.jumpHeight[entity]);
}

export function handleJump(
  entity: number,
  jumpPressed: number,
  currentTime: number,
  platform: number | null = null
): number {
  if (jumpPressed === 1) {
    Player.jumpBufferTime[entity] = currentTime;
  }

  if (canPerformJump(entity, currentTime)) {
    const jumpVelocity = calculateJumpVelocity(entity);
    CharacterMovement.velocityY[entity] = jumpVelocity;
    Player.isJumping[entity] = 1;
    Player.canJump[entity] = 0;
    Player.jumpCooldown[entity] = JUMP_CONSTANTS.cooldown;
    Player.jumpBufferTime[entity] = -10000;

    if (platform && platform > 0) {
      const tangentialVel = calculateTangentialVelocity(
        Player.inheritedAngVelX[entity],
        Player.inheritedAngVelY[entity],
        Player.inheritedAngVelZ[entity],
        Player.platformOffsetX[entity],
        Player.platformOffsetY[entity],
        Player.platformOffsetZ[entity]
      );

      Player.inheritedVelX[entity] += tangentialVel.x;
      Player.inheritedVelZ[entity] += tangentialVel.z;
    }

    return jumpVelocity;
  }

  return 0;
}

function calculateSlerpFactor(
  currentQuat: THREE.Quaternion,
  targetQuat: THREE.Quaternion,
  maxRotation: number
): number {
  const dotProduct = currentQuat.dot(targetQuat);
  const angle = 2 * Math.acos(Math.min(1, Math.abs(dotProduct)));
  return angle > 0.001 ? Math.min(1.0, maxRotation / angle) : 1.0;
}

export function updateRotation(
  entity: number,
  inputVector: THREE.Vector3,
  deltaTime: number,
  rotationData: {
    rotX: number;
    rotY: number;
    rotZ: number;
    rotW: number;
  }
): { x: number; y: number; z: number; w: number } {
  if (inputVector.length() <= JUMP_CONSTANTS.verticalVelocityThreshold) {
    return {
      x: rotationData.rotX,
      y: rotationData.rotY,
      z: rotationData.rotZ,
      w: rotationData.rotW,
    };
  }

  const targetYRotation = Math.atan2(inputVector.x, inputVector.z);
  _tmpEuler.set(0, targetYRotation, 0);
  _tmpQuat.setFromEuler(_tmpEuler);

  _tmpQuat2.set(
    rotationData.rotX,
    rotationData.rotY,
    rotationData.rotZ,
    rotationData.rotW
  );

  const maxRotationRadians = Player.rotationSpeed[entity] * deltaTime;
  const slerpFactor = calculateSlerpFactor(
    _tmpQuat2,
    _tmpQuat,
    maxRotationRadians
  );

  _tmpQuat2.slerp(_tmpQuat, slerpFactor);

  return {
    x: _tmpQuat2.x,
    y: _tmpQuat2.y,
    z: _tmpQuat2.z,
    w: _tmpQuat2.w,
  };
}
