import * as THREE from 'three';
import type { State } from '../../core';
import { Terrain } from '../terrain/components';
import {
  extractTerrainHeightmapImageData,
  getTerrainContext,
  sampleTerrainHeightGpuAligned,
} from '../terrain/utils';
import { Transform, WorldTransform } from '../transforms/components';

/**
 * Normal da superfície y = h(x,z) via diferenças centrais.
 * Convenção: n ∝ (-∂h/∂x, 1, -∂h/∂z), normalizado.
 */
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
  /** Altura Y no mundo no ponto (wx, wz): base do terreno + amostra do heightmap. */
  worldY: number;
  normal: THREE.Vector3;
}

/**
 * Verifica se a inclinação do terreno (ângulo da normal face a +Y) não excede `maxSlopeDeg`.
 * Plano horizontal: 0°; declive 45°: normal a 45° da vertical.
 */
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

/**
 * Usa o primeiro terreno inicializado no contexto (mesma ordem que `getTerrainHeightAt`).
 */
export function sampleTerrainSurface(
  state: State,
  wx: number,
  wz: number,
  eps: number
): TerrainSurfaceSample | null {
  const context = getTerrainContext(state);
  for (const [entity, data] of context) {
    if (!data.initialized) continue;
    const ox = data.worldOffset.x;
    const oz = data.worldOffset.z;
    const cfg = data.terrainLOD.getConfig();
    const imageData = extractTerrainHeightmapImageData(data.terrainLOD);
    const hmW = imageData?.width ?? 1024;
    const smoothing = state.hasComponent(entity, Terrain)
      ? Terrain.heightSmoothing[entity]
      : cfg.heightSmoothing;
    const spread = state.hasComponent(entity, Terrain)
      ? Terrain.heightSmoothingSpread[entity]
      : cfg.heightSmoothingSpread;
    const h = imageData
      ? sampleTerrainHeightGpuAligned(
          imageData,
          cfg.worldSize,
          cfg.maxHeight,
          wx,
          wz,
          true,
          ox,
          oz,
          hmW,
          smoothing,
          spread
        )
      : data.terrainLOD.getHeightAt(wx - ox, wz - oz);
    const ty = terrainBaseY(state, entity);
    /** Gradiente para declive: heightmap bruto (smoothing=0). Com smoothing>0 o shader achata o relevo e a normal fica quase +Y mesmo em encostas íngremes — o spawner aceitava sítios demasiado íngremes. */
    const heightAtRawSlope = (x: number, z: number) =>
      imageData
        ? sampleTerrainHeightGpuAligned(
            imageData,
            cfg.worldSize,
            cfg.maxHeight,
            x,
            z,
            true,
            ox,
            oz,
            hmW,
            0,
            spread
          )
        : data.terrainLOD.getHeightAt(x - ox, z - oz);
    const normal = normalFromHeightSampler(heightAtRawSlope, wx, wz, eps);
    return {
      terrainEntity: entity,
      worldY: ty + h,
      normal,
    };
  }
  return null;
}
