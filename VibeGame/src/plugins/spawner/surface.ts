import * as THREE from 'three';
import { defineQuery, type State } from '../../core';
import { Terrain, TerrainPad } from '../terrain/components';
import {
  getGroundBrushes,
  pointInPadCore,
  pointInRoadCarve,
} from '../terrain/brush-registry';
import { sampleHeightAt, type HeightSampler } from '../terrain/height-sampler';
import { meshSurfaceResolutionForPoint } from '../terrain/lod-select';
import { getTerrainContext } from '../terrain/utils';
import { Transform, WorldTransform } from '../transforms/components';
import { Lake, River } from '../water/components';
import { Road } from '../road/components';

const padPendingQuery = defineQuery([TerrainPad]);
const lakePendingQuery = defineQuery([Lake]);
const riverPendingQuery = defineQuery([River]);
const roadPendingQuery = defineQuery([Road]);

/**
 * Any ground mutation (<TerrainPad>, <Lake>, <River>, flatten <Road>) still
 * waiting to stamp into the height sampler. Spawning/placing before they apply
 * races the mutation: an entity sampled on pre-pad ground ends up buried (or
 * floating) once the pad flattens / the carve digs.
 *
 * Carve/flatten must never time out — a late Road would leave trees floating.
 * Only an undecoded heightmap (broken URL, no flatten in the scene) may fall
 * through after {@link placementDeferDecision}'s frame budget.
 */
export function isGroundMutationPending(state: State): boolean {
  for (const eid of padPendingQuery(state.world)) {
    if (TerrainPad.applied[eid] !== 1) return true;
  }
  for (const eid of lakePendingQuery(state.world)) {
    if (Lake.applied[eid] !== 1) return true;
  }
  for (const eid of riverPendingQuery(state.world)) {
    if (River.applied[eid] !== 1) return true;
  }
  // Only roads that flatten the sampler block spawn — pure decals are fine.
  for (const eid of roadPendingQuery(state.world)) {
    if (Road.flatten[eid] === 1 && Road.applied[eid] !== 1) return true;
  }
  return false;
}

/** Terrain declares a heightmap URL but the sampler has not decoded yet. */
export function isTerrainHeightmapPending(state: State): boolean {
  const tctx = getTerrainContext(state);
  for (const [, data] of tctx) {
    if (data.heightmapUrl && data.sampler.data === null) return true;
  }
  return false;
}

/** Heightmap decoded and every pad/lake/river/flatten-road has stamped. */
export function isGroundReadyForPlacement(state: State): boolean {
  return !isTerrainHeightmapPending(state) && !isGroundMutationPending(state);
}

/**
 * Whether spawn/place may sample the live surface this frame.
 *
 * Flatten/carve (`isGroundMutationPending`) always waits — never place on the
 * pre-carve sampler. An undecoded heightmap may time out so a missing file
 * does not hang the world forever.
 */
export function placementDeferDecision(
  state: State,
  heightmapDeferFrames: number,
  maxHeightmapDeferFrames: number
): 'wait' | 'place' {
  if (isGroundMutationPending(state)) return 'wait';
  if (
    isTerrainHeightmapPending(state) &&
    heightmapDeferFrames < maxHeightmapDeferFrames
  ) {
    return 'wait';
  }
  return 'place';
}

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
 *
 * Callers should pass a resolution from {@link meshSurfaceResolutionForPoint}
 * when a density map is present — featured regions (river/pad/road) render
 * leaf chunks finer than `Terrain.resolution`, and sampling only the base
 * lattice leaves props floating above the visible carve/falloff.
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
  /**
   * True when `worldY` is the analytic plane of a TerrainPad flat core (not
   * the mesh lattice). Placement must use `worldY` verbatim for these —
   * re-sampling the lattice would blend the pad edge with untouched terrain
   * (coarse LOD step at distance) and float/sink props by up to ~1 m.
   */
  padPlane?: boolean;
  /**
   * True when `worldY` is the analytic carved heightfield along a flatten-road
   * corridor (bed + talude), not the mesh lattice. Quiet leaf next to a
   * density-boosted road leaf otherwise samples the uncut plateau while the
   * camera sees the bank.
   */
  roadCarve?: boolean;
}

/** Slope angle in radians between the surface normal and vertical (+Y). */
export function slopeAngleRad(normal: THREE.Vector3): number {
  return Math.acos(Math.min(1, Math.max(-1, normal.y)));
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
const _avgSurfaceNormal = /*@__PURE__*/ new THREE.Vector3();
const SURFACE_NORMAL_WEIGHTS = [1, 1, 1, 1, 6, 1, 1, 1, 1] as const;

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

/** Up normal shared by pad-plane samples. */
const _padPlaneNormal = new THREE.Vector3(0, 1, 0);

/**
 * World Y of a TerrainPad's flat core plane at (field-local x, z), or null
 * when the point lies outside every applied pad core. Brushes register the
 * resolved pad plane (`targetY`, field-local) at apply time — before that
 * there is no plane and callers fall back to the mesh lattice.
 */
function padCoreWorldY(
  state: State,
  localX: number,
  localZ: number,
  terrainBaseYValue: number
): number | null {
  for (const brush of getGroundBrushes(state)) {
    if (brush.kind !== 'pad' || brush.targetY === undefined) continue;
    if (pointInPadCore(brush, localX, localZ)) {
      return terrainBaseYValue + brush.targetY;
    }
  }
  return null;
}

/**
 * Analytic heightfield Y on a flatten-road carve (bed + talude), or null
 * outside every road brush. Same failure mode as {@link padCoreWorldY}: the
 * mesh lattice on a quiet neighbour leaf interpolates the uncut plateau.
 */
function roadCarveWorldY(
  state: State,
  localX: number,
  localZ: number,
  sampler: HeightSampler,
  terrainBaseYValue: number
): number | null {
  for (const brush of getGroundBrushes(state)) {
    if (brush.kind !== 'road') continue;
    if (!pointInRoadCarve(brush, localX, localZ)) continue;
    return terrainBaseYValue + sampleHeightAt(sampler, localX, localZ);
  }
  return null;
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
    const ty = terrainBaseY(state, entity);

    // Inside a TerrainPad's flat core the ground is exactly the pad plane.
    // Sampling the mesh lattice here blends the pad edge with untouched
    // terrain (coarse LOD step at distance), which left small pads with
    // furniture floating/sunk by up to ~1 m. The brush registry already has
    // the analytic plane — anchor to it and skip the lattice entirely.
    const padPlane = padCoreWorldY(state, wx - ox, wz - oz, ty);
    if (padPlane !== null) {
      return {
        terrainEntity: entity,
        worldY: padPlane,
        normal: _padPlaneNormal,
        padPlane: true,
      };
    }

    const heightAtRawSlope = (x: number, z: number) =>
      sampleHeightAt(data.sampler, x - ox, z - oz);

    const carveY = roadCarveWorldY(state, localX, localZ, data.sampler, ty);
    if (carveY !== null) {
      return {
        terrainEntity: entity,
        worldY: carveY,
        normal: normalFromHeightSampler(heightAtRawSlope, wx, wz, effectiveEps),
        roadCarve: true,
      };
    }

    const meshRes = meshSurfaceResolutionForPoint(
      Terrain.resolution[entity],
      Terrain.levels[entity],
      data.density,
      localX,
      localZ
    );
    const h = sampleMeshSurfaceHeight(data.sampler, localX, localZ, meshRes);

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
    const ty = terrainBaseY(state, entity);

    // Inside a TerrainPad's flat core the ground is exactly the pad plane.
    // Sampling the mesh lattice here blends the pad edge with untouched
    // terrain (coarse LOD step at distance), which left small pads with
    // furniture floating/sunk by up to ~1 m. The brush registry already has
    // the analytic plane — anchor to it and skip the lattice entirely.
    const padPlane = padCoreWorldY(state, wx - ox, wz - oz, ty);
    if (padPlane !== null) {
      return {
        terrainEntity: entity,
        worldY: padPlane,
        normal: _padPlaneNormal,
        slopeAngleRad: 0,
        padPlane: true,
      };
    }

    const heightAtRawSlope = (x: number, z: number) =>
      sampleHeightAt(data.sampler, x - ox, z - oz);

    const carveY = roadCarveWorldY(state, localX, localZ, data.sampler, ty);
    if (carveY !== null) {
      let totalWeight = 0;
      const avgNormal = _avgSurfaceNormal.set(0, 0, 0);
      for (let row = 0; row < 3; row++) {
        for (let col = 0; col < 3; col++) {
          const w = SURFACE_NORMAL_WEIGHTS[row * 3 + col]!;
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
      return {
        terrainEntity: entity,
        worldY: carveY,
        normal: avgNormal,
        slopeAngleRad: slopeAngleRad(avgNormal),
        roadCarve: true,
      };
    }

    const meshRes = meshSurfaceResolutionForPoint(
      Terrain.resolution[entity],
      Terrain.levels[entity],
      data.density,
      localX,
      localZ
    );
    const h = sampleMeshSurfaceHeight(data.sampler, localX, localZ, meshRes);

    let totalWeight = 0;
    const avgNormal = _avgSurfaceNormal.set(0, 0, 0);

    for (let row = 0; row < 3; row++) {
      for (let col = 0; col < 3; col++) {
        const w = SURFACE_NORMAL_WEIGHTS[row * 3 + col]!;
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
