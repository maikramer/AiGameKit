import * as THREE from 'three';
import { eulerToQuaternion, quaternionToEuler } from '../../core/math';

export interface ParsedTransformParts {
  pos: [number, number, number];
  euler: [number, number, number];
  scale: [number, number, number];
}

export function defaultTransformParts(): ParsedTransformParts {
  return {
    pos: [0, 0, 0],
    euler: [0, 0, 0],
    scale: [1, 1, 1],
  };
}

export function parseTransformAttr(
  raw: string | undefined
): ParsedTransformParts {
  const out = defaultTransformParts();
  if (!raw?.trim()) return out;
  for (const part of raw.split(';')) {
    const p = part.trim();
    if (!p) continue;
    const colon = p.indexOf(':');
    if (colon < 0) continue;
    const key = p.slice(0, colon).trim().toLowerCase();
    const val = p.slice(colon + 1).trim();
    const nums = val.split(/\s+/).map((n) => parseFloat(n));
    if (key === 'pos' && nums.length >= 3) {
      out.pos = [nums[0], nums[1], nums[2]];
    } else if (key === 'euler' && nums.length >= 3) {
      out.euler = [nums[0], nums[1], nums[2]];
    } else if (key === 'scale') {
      if (nums.length === 1 && !Number.isNaN(nums[0])) {
        out.scale = [nums[0], nums[0], nums[0]];
      } else if (nums.length >= 3) {
        out.scale = [nums[0], nums[1], nums[2]];
      }
    }
  }
  return out;
}

export function formatTransformAttr(parts: ParsedTransformParts): string {
  return (
    `pos: ${parts.pos[0]} ${parts.pos[1]} ${parts.pos[2]}; ` +
    `euler: ${parts.euler[0]} ${parts.euler[1]} ${parts.euler[2]}; ` +
    `scale: ${parts.scale[0]} ${parts.scale[1]} ${parts.scale[2]}`
  );
}

const _qAlign = new THREE.Quaternion();
const _qYaw = new THREE.Quaternion();
const _qTemplate = new THREE.Quaternion();
const _qFinal = new THREE.Quaternion();
const _up = new THREE.Vector3(0, 1, 0);

/**
 * Monta quaternion: alinha +Y ao normal do terreno, yaw opcional em torno do normal, depois euler do template (graus, ordem XYZ).
 */
export function composeSpawnRotation(
  normal: THREE.Vector3,
  alignToTerrain: boolean,
  randomYawRadians: number,
  templateEulerDeg: [number, number, number]
): { x: number; y: number; z: number } {
  const tq = eulerToQuaternion(
    templateEulerDeg[0],
    templateEulerDeg[1],
    templateEulerDeg[2]
  );
  _qTemplate.set(tq.x, tq.y, tq.z, tq.w);

  if (alignToTerrain) {
    const n = normal.clone();
    if (n.lengthSq() < 1e-12) {
      n.set(0, 1, 0);
    } else {
      n.normalize();
    }
    if (Math.abs(n.y) > 0.9999) {
      _qAlign.identity();
    } else {
      _qAlign.setFromUnitVectors(_up, n);
    }
    if (randomYawRadians !== 0) {
      _qYaw.setFromAxisAngle(n, randomYawRadians);
    } else {
      _qYaw.identity();
    }
    // q_yaw * q_align * q_template: template → alinhar +Y ao normal → yaw em torno do tronco.
    _qFinal.copy(_qYaw).multiply(_qAlign).multiply(_qTemplate);
  } else {
    if (randomYawRadians !== 0) {
      const yq = eulerToQuaternion(0, (randomYawRadians * 180) / Math.PI, 0);
      _qYaw.set(yq.x, yq.y, yq.z, yq.w);
    } else {
      _qYaw.identity();
    }
    _qFinal.copy(_qYaw).multiply(_qTemplate);
  }

  return quaternionToEuler(
    _qFinal.x,
    _qFinal.y,
    _qFinal.z,
    _qFinal.w
  );
}
