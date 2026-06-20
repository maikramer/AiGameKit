import * as THREE from 'three';
import type { State } from '../../core';
import { Terrain } from '../terrain/components';
import { sampleHeightAt, type HeightSampler } from '../terrain/height-sampler';
import { getTerrainContext } from '../terrain/utils';
import { Transform, WorldTransform } from '../transforms/components';

/**
 * Elevation of the *rendered* terrain surface (the LOD mesh) at a field-local
 * (x, z), as opposed to the full-resolution analytic height from
 * {@link sampleHeightAt}.
 *
 * The terrain mesh only samples the heightfield at its vertices — spaced
 * `worldSize / baseResolution` apart — and draws flat triangles between them
 * (see `buildChunkGeometry`). That spacing is constant across LOD levels (the
 * per-level size halving and resolution halving cancel out), so we can
 * reproduce the visible surface by sampling the heightmap on that same lattice
 * and interpolating across the matching triangle.
 *
 * Anchoring spawned objects to this height (instead of the finer analytic one)
 * keeps them flush with what is actually drawn: on peaks/ridges that fall
 * between mesh vertices the analytic height sits above the flat triangle, which
 * is exactly why a subset of trees appeared to float.
 */
export function sampleMeshSurfaceHeight(
  sampler: HeightSampler,
  localX: number,
  localZ: number,
  baseResolution: number
): number {
  const res = Math.floor(baseResolution);
  // No usable mesh lattice (flat field or bad config) → analytic height.
  if (res < 1 || !sampler.data) {
    return sampleHeightAt(sampler, localX, localZ);
  }

  const half = sampler.worldSize / 2;
  const step = sampler.worldSize / res;
  const gx = (localX + half) / step;
  const gz = (localZ + half) / step;
  const x0 = Math.floor(gx);
  const z0 = Math.floor(gz);
  const fx = gx - x0;
  const fz = gz - z0;

  const lx0 = x0 * step - half;
  const lz0 = z0 * step - half;
  const lx1 = lx0 + step;
  const lz1 = lz0 + step;

  // Quad corners, matching buildChunkGeometry's vertex layout / triangulation:
  // a=(x,z) b=(x+1,z) c=(x,z+1) d=(x+1,z+1); triangles (a,c,b) and (b,c,d).
  const hA = sampleHeightAt(sampler, lx0, lz0);
  const hB = sampleHeightAt(sampler, lx1, lz0);
  const hC = sampleHeightAt(sampler, lx0, lz1);
  const hD = sampleHeightAt(sampler, lx1, lz1);

  if (fx + fz <= 1) {
    return hA + fx * (hB - hA) + fz * (hC - hA);
  }
  return hD + (1 - fx) * (hC - hD) + (1 - fz) * (hB - hD);
}

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
  _n0.set(-dhdx, 1, -dhdz);
  if (_n0.lengthSq() < 1e-12) {
    return _n1.set(0, 1, 0);
  }
  return _n0.normalize();
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
 * one edge floating on sloped terrain. When `actualTiltRad` is provided the
 * sink is scaled down by how much the object already leans toward the surface
 * — a fully-aligned object needs almost no sink, while an upright one on a
 * steep slope sinks deeper to hide the floating edge.
 */
export function sinkOffsetForSlope(
  slopeRad: number,
  objectHalfWidth: number,
  actualTiltRad?: number
): number {
  const residualSlope =
    actualTiltRad !== undefined
      ? Math.max(0, slopeRad - actualTiltRad)
      : slopeRad;
  return Math.sin(residualSlope) * objectHalfWidth;
}

const _alignUp = /*@__PURE__*/ new THREE.Vector3(0, 1, 0);
const _alignNormal = /*@__PURE__*/ new THREE.Vector3();
const _tiltAxis = /*@__PURE__*/ new THREE.Vector3();
const _qTilt = /*@__PURE__*/ new THREE.Quaternion();
const _qYawTrunk = /*@__PURE__*/ new THREE.Quaternion();
const _eOut = /*@__PURE__*/ new THREE.Euler(0, 0, 0, 'XYZ');

// Scratch for normalFromHeightSampler: the function is called sequentially
// (never reentrant) and every caller consumes the result before the next call,
// so the returned Vector3 is reused instead of allocating per probe.
const _n0 = /*@__PURE__*/ new THREE.Vector3();
const _n1 = /*@__PURE__*/ new THREE.Vector3();

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
 *    linearly and is clamped to `maxTiltRad` (default π/3 ≈ 60°) so trees
 *    follow the terrain surface naturally without lying flat on extreme
 *    cliffs. The profile's `maxSlopeDeg` gate already filters out
 *    unreasonably steep spawn positions.
 *  - The tilt leans toward the terrain's fall-line (the surface normal), then
 *    yaw is applied about the (tilted) trunk axis.
 */
export function partialAlignEuler(
  normal: THREE.Vector3,
  yawRad: number,
  slopeRad: number,
  minSlopeRad = 0.087,
  maxTiltRad = Math.PI / 3
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
    const baseRes = Terrain.resolution[entity];
    const h = sampleMeshSurfaceHeight(data.sampler, localX, localZ, baseRes);
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
    const baseRes = Terrain.resolution[entity];
    const h = sampleMeshSurfaceHeight(data.sampler, localX, localZ, baseRes);
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
