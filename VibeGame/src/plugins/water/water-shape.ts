import * as THREE from 'three';
import type { HeightSampler } from '../terrain/height-sampler';
import type { WorldAabb } from '../terrain/density-map';
import { getTerrainContext } from '../terrain/utils';
import { rebuildTerrainDerivatives } from '../terrain/height-brush';
import {
  applyCorridorDensity,
  applyFeatureDensity,
  densityLeafPad,
} from '../terrain/ground-mutation';
import {
  registerGroundBrush,
  unregisterGroundBrush,
  type GroundBrush,
} from '../terrain/brush-registry';
import { getRenderingContext } from '../rendering';
import { refreshChunkResolutions } from '../terrain/systems';
import { Terrain } from '../terrain/components';
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
  /**
   * Optional polyline footprint for the density boost. When present, the
   * boost is stamped per segment (AABB of each segment ± reach) instead of
   * the shape's global AABB — a map-length diagonal river's global AABB
   * covers a huge rectangle, and with the ×8 boost ceiling that would
   * upres half the map. Lakes (small AABBs) can skip this.
   */
  densityPath?(): { path: number[]; reach: number } | null;
  /** Carve the shape into the sampler in place (heights only go down). */
  carve(sampler: HeightSampler): WaterShapeResult;
  /** Surface mesh geometry. Must set the `aWaterT` attribute (0=center, 1=margin). */
  buildGeometry(): THREE.BufferGeometry;
  /**
   * World-space position for the mesh. `worldOffsetY` is the terrain field's
   * world Y offset (the only world datum the shape doesn't own). Lakes place a
   * flat origin-centred fan → `{ x: centreX, y: worldOffsetY + localWaterY, z: centreZ }`.
   * Rivers bake per-node surface Y into the ribbon vertices → `{ x:0, y: worldOffsetY, z:0 }`
   * (the field offset lifts field-local vertex Y into world).
   */
  worldOrigin(worldOffsetY: number): { x: number; y: number; z: number };
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
  /**
   * Where the shallow→deep colour ramp starts/ends along `aWaterT` (1 = margin,
   * 0 = centre/axis). Lakes default to [0, 1] (gentle radial gradient); narrow
   * shapes like rivers want the deep colour to win sooner ([0.35, 0.85]) or the
   * whole ribbon reads as washed-out shallow tint.
   */
  depthRampStart?: number;
  depthRampEnd?: number;
  /** Strength of the sky-tint fresnel mix (default 0.5). Rivers are viewed at
   *  grazing angles almost permanently, so they use a lower value. */
  fresnelStrength?: number;
  /** Additive sparkle glint strength (default 0.3). */
  sparkleStrength?: number;
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
 * @param createMaterial Factory `(config) => Material` (CustomShaderMaterial;
 *                       its `uniforms` are read directly off the returned
 *                       material — no onBeforeCompile shader-ref plumbing).
 * @param config         Material config (color/opacity/ripple/wave).
 * @param cars           Sidecar map (shared WaterSideCars) for uTime animation.
 * @returns true if applied this frame, false if deferred (sampler not ready).
 */
export function applyWaterShape(
  state: import('../../core').State,
  entity: number,
  shape: WaterShape,
  createMaterial: (config: WaterMaterialConfig) => WaterMaterial,
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

  // 1. Density stamp (shared ground-mutation) + refresh chunk resolutions.
  // Corridors get leafPad so chunk borders share boost (no T-junction stripes).
  if (data.density) {
    const levels = Math.max(1, Terrain.levels[field.entity] || 1);
    const worldSize = Terrain.worldSize[field.entity] || data.sampler.worldSize;
    const leafPad = densityLeafPad(worldSize, levels);
    const boost = shape.densityBoost();
    const seg = shape.densityPath?.();
    if (seg && seg.path.length >= 4) {
      applyCorridorDensity(data.density, seg.path, seg.reach, boost, leafPad);
    } else {
      applyFeatureDensity(data.density, shape.computeAabb(), boost, leafPad);
    }
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

  // 3. Mark terrain derivatives dirty (helper partilhado com pads/estradas).
  rebuildTerrainDerivatives(state, field.entity, data);

  // 3b. Ground-brush footprint (navmesh adaptive mesh + river walls).
  const aabb = shape.computeAabb();
  const brush: GroundBrush = {
    kind: shape.densityPath?.() ? 'river' : 'lake',
    minX: aabb.minX,
    maxX: aabb.maxX,
    minZ: aabb.minZ,
    maxZ: aabb.maxZ,
  };
  const dens = shape.densityPath?.();
  if (dens && dens.path.length >= 4) {
    brush.kind = 'river';
    brush.path = dens.path.slice();
    brush.halfWidth = dens.reach;
  } else {
    brush.kind = 'lake';
  }
  registerGroundBrush(state, brush);

  // 4. Spawn surface mesh with the shape-agnostic material.
  const scene = getRenderingContext(state).scene;
  const material = createMaterial(config);
  const mesh = new THREE.Mesh(shape.buildGeometry(), material);
  // The shape owns the full mesh position: lakes lift a flat fan to the surface,
  // rivers bake per-node Y into the ribbon and only need the field offset.
  const origin = shape.worldOrigin(data.worldOffset.y);
  mesh.position.set(origin.x, origin.y, origin.z);
  mesh.renderOrder = 2;
  mesh.receiveShadow = true;
  scene.add(mesh);

  // 5. Register the water body (resolved worldY = field offset + local waterY).
  const body = shape.toWaterBody(worldWaterY);
  // Prefer waterline half-width from the body for river ribbon walls.
  if (body.kind === 'river') {
    brush.halfWidth = body.width / 2;
    brush.path = [];
    for (const p of body.path) {
      brush.path.push(p[0] - data.worldOffset.x, p[1] - data.worldOffset.z);
    }
  }
  registerWaterBody(state, body);
  cars.set(entity, { mesh, material, body, brush });

  // 6. Cleanup on destroy.
  state.onDestroy(entity, () => {
    const c = cars.get(entity);
    if (!c) return;
    cars.delete(entity);
    c.mesh.removeFromParent();
    c.mesh.geometry.dispose();
    c.material.dispose();
    unregisterWaterBody(state, c.body);
    if (c.brush) unregisterGroundBrush(state, c.brush);
  });

  logger.info(
    `[water] ${entity} applied: waterY=${worldWaterY.toFixed(1)} rimY=${result.rimY.toFixed(1)}`
  );
  return true;
}

/**
 * A `CustomShaderMaterial` instance exposes its live `uniforms` directly (no
 * onBeforeCompile shader-ref indirection, which used to go stale across
 * recompiles — see the `uTime` freeze noted in project history).
 */
export interface WaterMaterial extends THREE.Material {
  uniforms: Record<string, { value: unknown }>;
}

/** Sidecar holding the live mesh/material/body for animation + cleanup. */
export interface WaterSideCar {
  mesh: THREE.Mesh;
  material: WaterMaterial;
  body: import('./registry').WaterBody;
  brush?: GroundBrush;
}
