import * as THREE from 'three';
import type { State } from '../../core';
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
    const cfg = data.terrainLOD.getConfig();

    const effectiveEps = surfaceEpsilonAuto
      ? Math.max(0.75, cfg.worldSize / (cfg.resolution * 4))
      : eps;

    const localX = wx - ox;
    const localZ = wz - oz;
    const h = data.terrainLOD.getHeightAt(localX, localZ);
    const ty = terrainBaseY(state, entity);

    const heightAtRawSlope = (x: number, z: number) =>
      data.terrainLOD.getHeightAt(x - ox, z - oz);

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
