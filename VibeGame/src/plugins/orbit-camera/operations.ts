import { Transform, syncEulerFromQuaternion } from '../transforms';
import * as THREE from 'three';
import { OrbitCamera } from './components';
import { normalizeAngle, shortestAngleDiff, smoothLerp } from './math';

const _tmpSpherical = new THREE.Spherical();
const _tmpVec3 = new THREE.Vector3();
const _tmpMat4 = new THREE.Matrix4();
const _tmpQuat = new THREE.Quaternion();
const _upVec = new THREE.Vector3(0, 1, 0);

export function smoothCameraRotation(
  cameraEntity: number,
  deltaTime: number
): void {
  const lerpFactor = smoothLerp(
    OrbitCamera.smoothness[cameraEntity],
    deltaTime
  );

  const yawDiff = shortestAngleDiff(
    OrbitCamera.currentYaw[cameraEntity],
    OrbitCamera.targetYaw[cameraEntity]
  );
  OrbitCamera.currentYaw[cameraEntity] += yawDiff * lerpFactor;
  OrbitCamera.currentYaw[cameraEntity] = normalizeAngle(
    OrbitCamera.currentYaw[cameraEntity]
  );

  OrbitCamera.currentPitch[cameraEntity] +=
    (OrbitCamera.targetPitch[cameraEntity] -
      OrbitCamera.currentPitch[cameraEntity]) *
    lerpFactor;

  OrbitCamera.currentDistance[cameraEntity] +=
    (OrbitCamera.targetDistance[cameraEntity] -
      OrbitCamera.currentDistance[cameraEntity]) *
    lerpFactor;
}

export function calculateCameraPosition(
  cameraEntity: number,
  targetPosition: THREE.Vector3
): THREE.Vector3 {
  const distance = OrbitCamera.currentDistance[cameraEntity];
  const yawAngle = OrbitCamera.currentYaw[cameraEntity];
  const polarAngle = Math.PI / 2 - OrbitCamera.currentPitch[cameraEntity];

  _tmpSpherical.set(distance, polarAngle, yawAngle);
  _tmpVec3.setFromSpherical(_tmpSpherical).add(targetPosition);

  return _tmpVec3;
}

export function updateCameraTransform(
  cameraEntity: number,
  cameraPosition: THREE.Vector3,
  targetPosition: THREE.Vector3
): void {
  Transform.posX[cameraEntity] = cameraPosition.x;
  Transform.posY[cameraEntity] = cameraPosition.y;
  Transform.posZ[cameraEntity] = cameraPosition.z;

  _tmpMat4.lookAt(cameraPosition, targetPosition, _upVec);
  _tmpQuat.setFromRotationMatrix(_tmpMat4);

  Transform.rotX[cameraEntity] = _tmpQuat.x;
  Transform.rotY[cameraEntity] = _tmpQuat.y;
  Transform.rotZ[cameraEntity] = _tmpQuat.z;
  Transform.rotW[cameraEntity] = _tmpQuat.w;

  syncEulerFromQuaternion(Transform, cameraEntity);
}
