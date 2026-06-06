import * as THREE from 'three';
import type { State } from '../../core';
import { sampleHeightAt } from '../terrain/height-sampler';
import { getTerrainContext } from '../terrain/utils';
import { Transform, WorldTransform } from '../transforms/components';

export function normalFromHeightSampler(
  heightAt: (x: number, z: number) => number,
  wx: number,
  wz: number,
  eps: number
): THREE.Vector3 {
  const safeEps = Math.max(eps, 1e-4);
  const hL = heightAt(wx - safeEps, wz);
  const hR = heightAt(wx + safeEps, wz);
  const hD = heightAt(wx, wz - safeEps);
  const hU = heightAt(wx, wz + safeEps);
  const dhdx = (hR - hL) / (2 * safeEps);
  const dhdz = (hU - hD) / (2 * safeEps);
  const v = new THREE.Vector3(-dhdx, 1, -dhdz);
  if (v.lengthSq() < 1e-12) {
    return new THREE.Vector3(0, 1, 0);
  }
  return v.normalize();
}

export interface TerrainSurfaceSample {
  terrainEntity: number;
  worldY: number;
  normal: THREE.Vector3;
}

/** Slope angle in radians between the surface normal and vertical (+Y). */
export function slopeAngleRad(normal: THREE.Vector3): number {
  return Math.acos(Math.min(1, Math.max(-1, normal.y)));
}

/**
 * Compute a sink offset that slightly buries the object to compensate for
 * one edge floating on sloped terrain. The offset is proportional to
 * sin(slopeAngle) * objectRadius, so wider objects on steeper slopes
 * sink more. The result is always >= 0 (downward in world Y).
 */
export function sinkOffsetForSlope(
  slopeRad: number,
  objectHalfWidth: number
): number {
  return Math.sin(slopeRad) * objectHalfWidth;
}

const _alignUp = /*@__PURE__*/ new THREE.Vector3(0, 1, 0);
const _alignNormal = /*@__PURE__*/ new THREE.Vector3();
const _tiltAxis = /*@__PURE__*/ new THREE.Vector3();
const _qTilt = /*@__PURE__*/ new THREE.Quaternion();
const _qYawTrunk = /*@__PURE__*/ new THREE.Quaternion();
const _eOut = /*@__PURE__*/ new THREE.Euler(0, 0, 0, 'XYZ');

/**
 * Compute a partial terrain-alignment Euler (in RADIANS, XYZ order) for
 * instanced vegetation.
 *
 * The returned triple is consumed directly by `Object3D.rotation.set(...)`
 * (default XYZ Euler order), so it must be expressed in radians — returning
 * degrees here makes every instance wrap to a near-random orientation, which
 * looks like trees lying flat on the ground.
 *
 * Behaviour:
 *  - Below `minSlopeRad` (or on effectively flat ground) → upright, yaw only.
 *  - Between min slope and `maxTiltRad` worth of slope, the lean blends
 *    linearly and is clamped to `maxTiltRad` so trees lean gently on moderate
 *    slopes but never look like they're falling over.
 *  - The tilt leans toward the terrain's fall-line (the surface normal), then
 *    yaw is applied about the (tilted) trunk axis.
 */
export function partialAlignEuler(
  normal: THREE.Vector3,
  yawRad: number,
  slopeRad: number,
  minSlopeRad = 0.087,
  maxTiltRad = 0.26
): [number, number, number] {
  // Flat enough → keep upright but still honour random yaw about +Y.
  if (slopeRad < minSlopeRad || normal.y > 0.9999) {
    return [0, yawRad, 0];
  }

  _alignNormal.copy(normal);
  if (_alignNormal.lengthSq() < 1e-12) {
    return [0, yawRad, 0];
  }
  _alignNormal.normalize();

  // Blend the lean linearly from the min-slope threshold and clamp it so the
  // trunk never tilts past `maxTiltRad`, regardless of how steep the ground is.
  const denom = Math.max(1e-6, maxTiltRad - minSlopeRad);
  const t = Math.min(1, Math.max(0, (slopeRad - minSlopeRad) / denom));
  const tilt = t * maxTiltRad;
  if (tilt < 1e-6) {
    return [0, yawRad, 0];
  }

  // Horizontal axis to rotate +Y about so it leans toward the surface normal.
  _tiltAxis.crossVectors(_alignUp, _alignNormal);
  if (_tiltAxis.lengthSq() < 1e-12) {
    return [0, yawRad, 0];
  }
  _tiltAxis.normalize();
  _qTilt.setFromAxisAngle(_tiltAxis, tilt);

  // Yaw about the trunk (local +Y, applied before the tilt) so trees still
  // rotate randomly around their own axis while leaning downhill.
  _qYawTrunk.setFromAxisAngle(_alignUp, yawRad);
  _qTilt.multiply(_qYawTrunk);

  _eOut.setFromQuaternion(_qTilt, 'XYZ');
  return [_eOut.x, _eOut.y, _eOut.z];
}

export function isNormalWithinSlopeLimit(
  normal: THREE.Vector3,
  maxSlopeDeg: number
): boolean {
  if (maxSlopeDeg >= 90 - 1e-6) return true;
  if (maxSlopeDeg <= 0) return normal.y >= 1 - 1e-5;
  const maxRad = THREE.MathUtils.degToRad(maxSlopeDeg);
  const cosMin = Math.cos(maxRad);
  return normal.y >= cosMin - 1e-5;
}

function terrainBaseY(state: State, terrainEntity: number): number {
  if (state.hasComponent(terrainEntity, WorldTransform)) {
    return WorldTransform.posY[terrainEntity];
  }
  return Transform.posY[terrainEntity];
}

export function sampleTerrainSurface(
  state: State,
  wx: number,
  wz: number,
  eps: number,
  surfaceEpsilonAuto = false
): TerrainSurfaceSample | null {
  const context = getTerrainContext(state);
  for (const [entity, data] of context) {
    if (!data.initialized) continue;
    const ox = data.worldOffset.x;
    const oz = data.worldOffset.z;

    const effectiveEps = surfaceEpsilonAuto
      ? Math.max(0.75, data.sampler.worldSize / (data.sampler.width * 4))
      : eps;

    const localX = wx - ox;
    const localZ = wz - oz;
    const h = sampleHeightAt(data.sampler, localX, localZ);
    const ty = terrainBaseY(state, entity);

    const heightAtRawSlope = (x: number, z: number) =>
      sampleHeightAt(data.sampler, x - ox, z - oz);

    const normal = normalFromHeightSampler(
      heightAtRawSlope,
      wx,
      wz,
      effectiveEps
    );
    return {
      terrainEntity: entity,
      worldY: ty + h,
      normal,
    };
  }
  return null;
}

/**
 * Sample terrain surface using a 3×3 grid of probes around (wx, wz) and
 * compute a weighted-average normal. The center probe (1,1) has 2× weight,
 * giving the actual spawn point more influence on the final normal.
 * Also returns the slope angle in radians.
 */
export function sampleTerrainSurfaceMatrix(
  state: State,
  wx: number,
  wz: number,
  eps: number,
  surfaceEpsilonAuto = false,
  matrixSpacing = 1.0
): (TerrainSurfaceSample & { slopeAngleRad: number }) | null {
  const context = getTerrainContext(state);
  for (const [entity, data] of context) {
    if (!data.initialized) continue;
    const ox = data.worldOffset.x;
    const oz = data.worldOffset.z;

    const effectiveEps = surfaceEpsilonAuto
      ? Math.max(0.75, data.sampler.worldSize / (data.sampler.width * 4))
      : eps;

    const localX = wx - ox;
    const localZ = wz - oz;
    const h = sampleHeightAt(data.sampler, localX, localZ);
    const ty = terrainBaseY(state, entity);

    const heightAtRawSlope = (x: number, z: number) =>
      sampleHeightAt(data.sampler, x - ox, z - oz);

    const CENTER_WEIGHT = 6;
    const weights = [
      [1, 1, 1],
      [1, CENTER_WEIGHT, 1],
      [1, 1, 1],
    ];
    let totalWeight = 0;
    const avgNormal = new THREE.Vector3(0, 0, 0);

    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        const w = weights[row]![col]!;
        const sx = wx + (col - 1) * matrixSpacing;
        const sz = wz + (row - 1) * matrixSpacing;
        const n = normalFromHeightSampler(
          heightAtRawSlope,
          sx,
          sz,
          effectiveEps
        );
        avgNormal.addScaledVector(n, w);
        totalWeight += w;
      }
    }

    if (totalWeight > 0) {
      avgNormal.divideScalar(totalWeight);
    }
    if (avgNormal.lengthSq() < 1e-12) {
      avgNormal.set(0, 1, 0);
    } else {
      avgNormal.normalize();
    }

    const angle = slopeAngleRad(avgNormal);

    return {
      terrainEntity: entity,
      worldY: ty + h,
      normal: avgNormal,
      slopeAngleRad: angle,
    };
  }
  return null;
}
