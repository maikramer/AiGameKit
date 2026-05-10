import * as THREE from 'three';
import { Rigidbody } from './components';

const _quat = new THREE.Quaternion();
const _euler = new THREE.Euler();

export function syncBodyQuaternionFromEuler(eid: number): void {
  _euler.set(
    Rigidbody.eulerX[eid],
    Rigidbody.eulerY[eid],
    Rigidbody.eulerZ[eid]
  );
  _quat.setFromEuler(_euler);
  Rigidbody.rotX[eid] = _quat.x;
  Rigidbody.rotY[eid] = _quat.y;
  Rigidbody.rotZ[eid] = _quat.z;
  Rigidbody.rotW[eid] = _quat.w;
}
