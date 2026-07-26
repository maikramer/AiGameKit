import * as THREE from 'three';
import type { State, XMLValue } from '../../core';
import { ParseContext } from '../../core/recipes/parse-context';
import {
  createEntityFromRecipe,
  processRecipeChildElements,
} from '../../core/recipes/parser';
import { sampleTerrainSurfaceMatrix, sinkOffsetForSlope } from './surface';
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import {
  composeSpawnRotation,
  defaultTransformParts,
  formatTransformAttr,
  parseTransformAttr,
} from './transform-merge';
import {
  getGltfLocalAABB,
  getGltfLocalYBounds,
  isGltfBoundsPrefetchInflight,
  prefetchGltfLocalYBounds,
} from '../gltf-xml/gltf-bounds-cache';
import { DistanceCull } from '../rendering/components';
import { Rigidbody } from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';
import { Transform } from '../transforms/components';
import {
  getVariationPreset,
  sampleVariation,
  writeSpawnVariation,
  type VariationVisualSpec,
} from '../spawn-variation';
import { TerrainSpawned } from './components';
import { setAabbPendingUrl } from './bounds-context';
import { templateVisualUrl } from './template-url';

const upNormal = new THREE.Vector3(0, 1, 0);

/**
 * Physics bodies live in world space, but the spawner only writes the
 * `transform` attribute — mirror the spawned pose into Rigidbody (same rule
 * as TerrainPlaceSystem) or the Rapier body is created at the world origin
 * while the visual sits on the terrain.
 */
function mirrorPoseToRigidbody(state: State, eid: number): void {
  if (!state.hasComponent(eid, Rigidbody)) return;
  Rigidbody.posX[eid] = Transform.posX[eid];
  Rigidbody.posY[eid] = Transform.posY[eid];
  Rigidbody.posZ[eid] = Transform.posZ[eid];
  Rigidbody.eulerX[eid] = Transform.eulerX[eid];
  Rigidbody.eulerY[eid] = Transform.eulerY[eid];
  Rigidbody.eulerZ[eid] = Transform.eulerZ[eid];
  syncBodyQuaternionFromEuler(eid);
  Rigidbody.poseDirty[eid] = 1;
}

function mergeTemplateAttributes(
  template: SpawnTemplateSpec,
  transformStr: string
): Record<string, XMLValue> {
  const out: Record<string, XMLValue> = {};
  for (const [k, v] of Object.entries(template.attributes)) {
    if (k === 'transform') continue;
    out[k] = v;
  }
  out.transform = transformStr;
  return out;
}

function resolveVisualSpec(
  spec: Pick<SpawnGroupSpec, 'variation'> | { variation?: VariationVisualSpec }
): VariationVisualSpec {
  return spec.variation ?? getVariationPreset('none');
}

/**
 * Spawns one entity from a template at world (wx, wy, wz) using the same rules as
 * {@link TerrainSpawnSystem} / spawn-group (scale jitter, terrain normal, AABB ground align).
 */
export function spawnTemplateAtTerrain(
  state: State,
  spec: Pick<
    SpawnGroupSpec,
    | 'alignToTerrain'
    | 'baseYOffset'
    | 'groundAlign'
    | 'randomYaw'
    | 'scaleDistribution'
    | 'scaleDiscreteValues'
    | 'scaleMin'
    | 'scaleMax'
    | 'scaleAxisMin'
    | 'scaleAxisMax'
    | 'yawDistribution'
    | 'yawDiscreteDeg'
    | 'surfaceEpsilon'
    | 'surfaceEpsilonAuto'
    | 'maxDistance'
  > & {
    /** Omit / `static` → edge-sink allowed; `dynamic` → never (creatures). */
    mode?: SpawnGroupSpec['mode'];
    variation?: VariationVisualSpec;
  },
  rand: () => number,
  wx: number,
  wy: number,
  wz: number,
  template: SpawnTemplateSpec
): void {
  const tmplTransform =
    typeof template.attributes.transform === 'string'
      ? template.attributes.transform
      : undefined;
  const parts = parseTransformAttr(tmplTransform);
  const base = defaultTransformParts();

  const sample = sampleVariation(
    {
      randomYaw: spec.randomYaw,
      scaleDistribution: spec.scaleDistribution,
      scaleDiscreteValues: spec.scaleDiscreteValues,
      scaleMin: spec.scaleMin,
      scaleMax: spec.scaleMax,
      scaleAxisMin: spec.scaleAxisMin,
      scaleAxisMax: spec.scaleAxisMax,
      yawDistribution: spec.yawDistribution,
      yawDiscreteDeg: spec.yawDiscreteDeg,
    },
    resolveVisualSpec(spec),
    rand,
    wx,
    wz
  );

  base.scale = [
    parts.scale[0] * sample.scaleUniform * sample.axisX,
    parts.scale[1] * sample.scaleUniform * sample.axisY,
    parts.scale[2] * sample.scaleUniform * sample.axisZ,
  ];

  const surface = sampleTerrainSurfaceMatrix(
    state,
    wx,
    wz,
    spec.surfaceEpsilon,
    spec.surfaceEpsilonAuto
  );
  const normal = surface?.normal ?? upNormal;

  const yawRad = sample.yawRad;

  const MIN_ALIGN_SLOPE_RAD = 0.06;
  const slopeSteepEnough =
    surface != null && surface.slopeAngleRad > MIN_ALIGN_SLOPE_RAD;
  const effectiveNormal =
    spec.alignToTerrain && slopeSteepEnough ? normal : upNormal;
  const euler = composeSpawnRotation(
    effectiveNormal,
    spec.alignToTerrain && slopeSteepEnough,
    yawRad,
    parts.euler
  );
  base.euler = [euler.x, euler.y, euler.z];

  const url = templateVisualUrl(template);
  const scaleY = Math.max(
    sample.scaleUniform * sample.axisY * parts.scale[1],
    1e-6
  );
  const scaleXZ = sample.scaleUniform * Math.max(sample.axisX, sample.axisZ);

  const isDynamic = spec.mode === 'dynamic';
  const aabb = getGltfLocalAABB(url);
  const halfWidth = aabb
    ? Math.max(aabb.maxX - aabb.minX, aabb.maxZ - aabb.minZ) / 2
    : 0.5;
  const scaledHalf = halfWidth * scaleXZ;
  // Edge-sink: static upright props only. Dynamics: no TerrainSpawned / lift /
  // sink — Creature CCT + terrain heightfield own runtime Y.
  const sink =
    !isDynamic && surface
      ? sinkOffsetForSlope(
          surface.slopeAngleRad,
          scaledHalf,
          spec.alignToTerrain ? surface.slopeAngleRad : 0
        )
      : 0;

  const foot = new THREE.Vector3();
  foot.set(0, 0, 0);
  // AABB lift / catch-up: statics with ground-align=aabb only.
  // Missing bounds (queued/in-flight/failed prefetch, or race before KTX2) →
  // plant with base-y-offset only and let TerrainSpawnBoundsCatchUpSystem
  // apply the lift once registerGltfLocalYBounds fills the cache.
  let needsBoundsCatchUp = false;
  if (!isDynamic && spec.groundAlign === 'aabb' && url) {
    const b = getGltfLocalYBounds(url);
    if (b) {
      const lift = -b.minY * scaleY;
      if (spec.alignToTerrain) {
        foot.copy(normal).multiplyScalar(lift);
      } else {
        foot.set(0, lift, 0);
      }
    } else {
      needsBoundsCatchUp = true;
      if (!isGltfBoundsPrefetchInflight(url)) {
        prefetchGltfLocalYBounds(url);
      }
    }
  }

  const footOffset = isDynamic ? 0 : spec.baseYOffset + foot.y;

  base.pos = [
    wx + parts.pos[0] + foot.x,
    wy + parts.pos[1] + footOffset - sink,
    wz + parts.pos[2] + foot.z,
  ];

  const transformStr = formatTransformAttr(base);
  const attrs = mergeTemplateAttributes(template, transformStr);

  const spawnedMeta = isDynamic
    ? null
    : {
        footOffset,
        surfaceEpsilon: spec.surfaceEpsilon,
        halfWidth: scaledHalf,
        alignToTerrain: spec.alignToTerrain,
        needsBoundsCatchUp,
        url,
        scaleY,
        normalY: effectiveNormal.y,
      };

  const tagLower = template.tagName.toLowerCase();
  const recipeName =
    tagLower === 'gameobject'
      ? 'GameObject'
      : tagLower === 'creature'
        ? 'Creature'
        : template.tagName;

  if (tagLower === 'gameobject' || tagLower === 'creature') {
    delete attrs.place;
  }

  const eid = createEntityFromRecipe(state, recipeName, attrs);
  const ch = template.entityChildren;
  if (ch?.length) {
    const context = new ParseContext(state);
    processRecipeChildElements(state, eid, recipeName, ch, context);
  }
  if (spec.maxDistance > 0) {
    state.addComponent(eid, DistanceCull);
    DistanceCull.maxDistance[eid] = spec.maxDistance;
  }
  writeSpawnVariation(state, eid, sample);
  mirrorPoseToRigidbody(state, eid);
  if (spawnedMeta) registerTerrainSpawned(state, eid, spawnedMeta);
}

/**
 * Attach `TerrainSpawned` for static/place props (not DynamicSpawner agents).
 */
function registerTerrainSpawned(
  state: State,
  eid: number,
  meta: {
    footOffset: number;
    surfaceEpsilon: number;
    halfWidth: number;
    alignToTerrain: boolean;
    needsBoundsCatchUp: boolean;
    url: string;
    scaleY: number;
    normalY: number;
  }
): void {
  state.addComponent(eid, TerrainSpawned);
  TerrainSpawned.yOffset[eid] = meta.footOffset;
  TerrainSpawned.surfaceEpsilon[eid] = meta.surfaceEpsilon;
  TerrainSpawned.halfWidth[eid] = meta.halfWidth;
  TerrainSpawned.alignToTerrain[eid] = meta.alignToTerrain ? 1 : 0;
  if (meta.needsBoundsCatchUp && meta.url) {
    TerrainSpawned.aabbPending[eid] = 1;
    TerrainSpawned.scaleY[eid] = meta.scaleY;
    TerrainSpawned.normalY[eid] = meta.normalY;
    setAabbPendingUrl(state, eid, meta.url);
  }
}
