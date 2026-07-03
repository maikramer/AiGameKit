import * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { getTerrainContext } from '../terrain/utils';
import { TerrainChunk } from '../terrain/components';
import { invalidateTerrainBvh } from '../bvh';
import { getRapierWorld } from '../physics';
import { getRenderingContext } from '../rendering';
import { applyOverride } from '../terrain/density-map';
import { refreshChunkResolutions } from '../terrain/systems';
import { registerWaterBody, unregisterWaterBody } from './registry';
import { logger } from '../../core/utils/logger';
import type { WaterBody } from './registry';

/** Result of carving a water shape into the terrain sampler. */
export interface WaterShapeResult {
  /** false when the sampler was flat/dataless — caller should retry next frame. */
  carved: boolean;
  /** Crest height of the rim/margins (metres). */
  rimY: number;
  /** Water surface height in field-local space (metres). */
  waterY: number;
}

/**
 * A water shape (lake bowl, river channel, future swamp/coastline) presented
 * through a uniform interface so the common apply flow (density boost, carve,
 * mark dirty, spawn mesh, register body, cleanup) can live in one helper.
 *
 * The only shape-specific behaviour is the "distance from the margin" metric:
 * lakes compute it radially, rivers laterally along the path. The water
 * material is shape-agnostic because that metric (`aWaterT`) is baked into
 * the geometry by `buildGeometry()`.
 */
export interface WaterShape {
  /** AABB in field-local coords (X/Z), for density boost + chunk invalidation. */
  computeAabb(): WorldAabb;
  /** Carve the shape into the sampler in place (heights only go down). */
  carve(sampler: HeightSampler): WaterShapeResult;
  /** Surface mesh geometry. Must set the `aWaterT` attribute (0=center, 1=margin). */
  buildGeometry(): THREE.BufferGeometry;
  /**
   * World-space X/Z offset to position the mesh at. Lakes return their centre
   * (the fan geometry is origin-centred); rivers return (0,0) (the ribbon
   * vertices already carry world coords). `applyWaterShape` sets
   * `mesh.position = (origin.x, worldWaterY, origin.z)`.
   */
  worldOrigin(): { x: number; z: number };
  /** Density boost for terrain chunks overlapping this shape (255 = max detail). */
  densityBoost(): number;
  /**
   * Water body for the registry. `worldWaterY` is the resolved world-space
   * surface height (= field.worldOffset.y + carve waterY), supplied by
   * `applyWaterShape` because only it knows the field's world offset.
   */
  toWaterBody(worldWaterY: number): WaterBody;
}

/** Visual config shared by all water shapes for the material. */
export interface WaterMaterialConfig {
  color: number;
  opacity: number;
  ripple: number;
  waveHeight: number;
  waveSpeed: number;
}

/**
 * Apply a water shape (lake or river) to the terrain: density boost, carve,
 * mark terrain derivatives dirty, spawn the surface mesh, register the body,
 * and wire cleanup. Shared by LakeApplySystem and RiverApplySystem so the
 * common flow lives in one place.
 *
 * To avoid a circular import (systems.ts ↔ water-shape.ts), the caller passes
 * a `createMaterial` factory that builds the shape-agnostic water material.
 *
 * @param state          ECS state.
 * @param entity         The water entity id (for sidecar + onDestroy).
 * @param shape          The WaterShape (LakeBowl / RiverChannel / future).
 * @param createMaterial Factory `(config, onShader) => MeshStandardMaterial`.
 * @param config         Material config (color/opacity/ripple/wave).
 * @param cars           Sidecar map (shared WaterSideCars) for uTime animation.
 * @returns true if applied this frame, false if deferred (sampler not ready).
 */
export function applyWaterShape(
  state: import('../../core').State,
  entity: number,
  shape: WaterShape,
  createMaterial: (
    config: WaterMaterialConfig,
    onShader: (shader: WaterShaderHandle) => void
  ) => import('three').MeshStandardMaterial,
  config: WaterMaterialConfig,
  cars: Map<number, WaterSideCar>
): boolean {
  // Find the terrain field with a decoded sampler.
  const context = getTerrainContext(state);
  let field: {
    entity: number;
    data: import('../terrain/utils').TerrainEntityData;
  } | null = null;
  for (const [fe, fd] of context) {
    if (fd.initialized && fd.sampler.data) {
      field = { entity: fe, data: fd };
      break;
    }
  }
  if (!field) return false;
  const { data } = field;

  // 1. Density boost + refresh chunk resolutions.
  if (data.density) {
    applyOverride(data.density, shape.computeAabb(), shape.densityBoost());
    refreshChunkResolutions(state, field.entity, data);
  }

  // 2. Carve.
  const result = shape.carve(data.sampler);
  if (!result.carved) {
    logger.warn(
      `[water] ${entity} carve skipped (sampler not ready); will retry next frame`
    );
    return false;
  }
  const worldWaterY = data.worldOffset.y + result.waterY;

  // 3. Mark terrain derivatives dirty.
  for (const chunk of data.chunks) TerrainChunk.meshDirty[chunk] = 1;
  const world = getRapierWorld(state);
  if (world) {
    for (const body of data.chunkColliders.values())
      world.removeRigidBody(body);
    data.chunkColliders.clear();
  }
  invalidateTerrainBvh(state, field.entity);

  // 4. Spawn surface mesh with the shape-agnostic material.
  const scene = getRenderingContext(state).scene;
  const material = createMaterial(config, (shader) => {
    const c = cars.get(entity);
    if (c) c.shader = shader;
  });
  const mesh = new THREE.Mesh(shape.buildGeometry(), material);
  const origin = shape.worldOrigin();
  mesh.position.set(origin.x, worldWaterY, origin.z);
  mesh.renderOrder = 2;
  mesh.receiveShadow = true;
  scene.add(mesh);

  // 5. Register the water body (resolved worldY = field offset + local waterY).
  const body = shape.toWaterBody(worldWaterY);
  registerWaterBody(state, body);
  cars.set(entity, { mesh, material, shader: null, body });

  // 6. Cleanup on destroy.
  state.onDestroy(entity, () => {
    const c = cars.get(entity);
    if (!c) return;
    cars.delete(entity);
    c.mesh.removeFromParent();
    c.mesh.geometry.dispose();
    c.material.dispose();
    unregisterWaterBody(state, c.body);
  });

  logger.info(
    `[water] ${entity} applied: waterY=${worldWaterY.toFixed(1)} rimY=${result.rimY.toFixed(1)}`
  );
  return true;
}

/** Minimal shader-handle type so water-shape.ts doesn't import systems.ts. */
export interface WaterShaderHandle {
  uniforms: Record<string, { value: unknown }>;
}

/** Sidecar holding the live mesh/material/shader/body for animation + cleanup. */
export interface WaterSideCar {
  mesh: THREE.Mesh;
  material: THREE.MeshStandardMaterial;
  shader: WaterShaderHandle | null;
  body: import('./registry').WaterBody;
}
