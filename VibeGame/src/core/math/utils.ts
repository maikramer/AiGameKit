import * as THREE from 'three';

const DEG2RAD = Math.PI / 180;
const RAD2DEG = 180 / Math.PI;

/** Scratch — reused across euler/quat conversions to avoid per-call GC. */
const _euler = new THREE.Euler();
const _quat = new THREE.Quaternion();
const _eulerOut = { x: 0, y: 0, z: 0 };
const _quatOut = { x: 0, y: 0, z: 0, w: 1 };

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

export function slerp(
  fromX: number,
  fromY: number,
  fromZ: number,
  fromW: number,
  toX: number,
  toY: number,
  toZ: number,
  toW: number,
  t: number
): { x: number; y: number; z: number; w: number } {
  let dot = fromX * toX + fromY * toY + fromZ * toZ + fromW * toW;

  let toXAdjusted = toX;
  let toYAdjusted = toY;
  let toZAdjusted = toZ;
  let toWAdjusted = toW;

  if (dot < 0) {
    dot = -dot;
    toXAdjusted = -toX;
    toYAdjusted = -toY;
    toZAdjusted = -toZ;
    toWAdjusted = -toW;
  }

  if (dot > 0.9995) {
    const x = fromX + t * (toXAdjusted - fromX);
    const y = fromY + t * (toYAdjusted - fromY);
    const z = fromZ + t * (toZAdjusted - fromZ);
    const w = fromW + t * (toWAdjusted - fromW);
    const len = Math.sqrt(x * x + y * y + z * z + w * w);
    return { x: x / len, y: y / len, z: z / len, w: w / len };
  }

  const theta = Math.acos(Math.min(1, Math.max(-1, dot)));
  const sinTheta = Math.sin(theta);
  const scale0 = Math.sin((1 - t) * theta) / sinTheta;
  const scale1 = Math.sin(t * theta) / sinTheta;

  return {
    x: scale0 * fromX + scale1 * toXAdjusted,
    y: scale0 * fromY + scale1 * toYAdjusted,
    z: scale0 * fromZ + scale1 * toZAdjusted,
    w: scale0 * fromW + scale1 * toWAdjusted,
  };
}

/** Write euler→quaternion into `out` (no allocation). Returns `out`. */
export function eulerToQuaternionInto(
  x: number,
  y: number,
  z: number,
  out: { x: number; y: number; z: number; w: number }
): { x: number; y: number; z: number; w: number } {
  _euler.set(x * DEG2RAD, y * DEG2RAD, z * DEG2RAD, 'XYZ');
  _quat.setFromEuler(_euler);
  out.x = _quat.x;
  out.y = _quat.y;
  out.z = _quat.z;
  out.w = _quat.w;
  return out;
}

export function eulerToQuaternion(
  x: number,
  y: number,
  z: number
): { x: number; y: number; z: number; w: number } {
  eulerToQuaternionInto(x, y, z, _quatOut);
  return { x: _quatOut.x, y: _quatOut.y, z: _quatOut.z, w: _quatOut.w };
}

/** Write quaternion→euler (degrees) into `out` (no allocation). Returns `out`. */
export function quaternionToEulerInto(
  x: number,
  y: number,
  z: number,
  w: number,
  out: { x: number; y: number; z: number }
): { x: number; y: number; z: number } {
  _quat.set(x, y, z, w);
  _euler.setFromQuaternion(_quat, 'XYZ');
  out.x = _euler.x * RAD2DEG;
  out.y = _euler.y * RAD2DEG;
  out.z = _euler.z * RAD2DEG;
  return out;
}

export function quaternionToEuler(
  x: number,
  y: number,
  z: number,
  w: number
): { x: number; y: number; z: number } {
  quaternionToEulerInto(x, y, z, w, _eulerOut);
  return { x: _eulerOut.x, y: _eulerOut.y, z: _eulerOut.z };
}
