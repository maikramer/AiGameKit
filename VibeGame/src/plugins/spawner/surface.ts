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

/**
 * Compute partial terrain alignment Euler for instanced vegetation.
 *
 * Below `minSlopeRad` → fully upright (no tilt). Between min and max slope,
 * blends linearly so trees lean gently on moderate slopes but never look
 * like they're falling over. The tilt direction follows the terrain's
 * fall-line (horizontal component of the surface normal).
 */
export function partialAlignEuler(
  normal: THREE.Vector3,
  yawRad: number,
  slopeRad: number,
  minSlopeRad = 0.087,
  maxTiltRad = 0.26
): [number, number, number] {
  if (slopeRad < minSlopeRad || normal.y > 0.9999) {
    return [0, 0, 0];
  }
  const t = Math.min(1, (slopeRad - minSlopeRad) / (maxTiltRad - minSlopeRad));
  const tilt = t * maxTiltRad;
  if (tilt < 1e-6) return [0, 0, 0];

  const nx = normal.x;
  const nz = normal.z;
  const hLen = Math.sqrt(nx * nx + nz * nz);
  if (hLen < 1e-6) return [0, 0, 0];

  const fallX = -nx / hLen;
  const fallZ = -nz / hLen;
  const axisX = -fallZ;
  const axisZ = fallX;

  const cx = Math.cos(tilt);
  const sx = Math.sin(tilt);

  const ex = sx * axisX;
  const ez = sx * axisZ;
  const ey = (1 - cx) * (fallX * axisX + fallZ * axisZ);

  return [
    THREE.MathUtils.radToDeg(Math.atan2(ex, cx - (ey * ex) / (1 + cx))),
    THREE.MathUtils.radToDeg(yawRad),
    THREE.MathUtils.radToDeg(Math.atan2(ez, cx - (ey * ez) / (1 + cx))),
  ];
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
