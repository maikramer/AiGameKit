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
  warnMissingGltfBoundsOnce,
} from '../gltf-xml/gltf-bounds-cache';
import { DistanceCull } from '../rendering/components';
import { Rigidbody } from '../physics/components';
import { syncBodyQuaternionFromEuler } from '../physics/utils';
import { Transform } from '../transforms/components';
import { TerrainSpawned } from './components';

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

/**
 * Spawns one entity from a template at world (wx, wy, wz) using the same rules as
 * {@link TerrainSpawnSystem} / spawn-group (scale jitter, terrain normal, AABB ground align).
 */
function pickScaleJitter(
  spec: Pick<
    SpawnGroupSpec,
    'scaleDistribution' | 'scaleDiscreteValues' | 'scaleMin' | 'scaleMax'
  >,
  rand: () => number
): number {
  if (
    spec.scaleDistribution === 'discrete' &&
    spec.scaleDiscreteValues.length > 0
  ) {
    const arr = spec.scaleDiscreteValues;
    return arr[Math.floor(rand() * arr.length)]!;
  }
  return spec.scaleMin + rand() * (spec.scaleMax - spec.scaleMin);
}

function pickYawRad(
  spec: Pick<
    SpawnGroupSpec,
    'randomYaw' | 'yawDistribution' | 'yawDiscreteDeg'
  >,
  rand: () => number
): number {
  if (!spec.randomYaw) return 0;
  if (spec.yawDistribution === 'discrete' && spec.yawDiscreteDeg.length > 0) {
    const arr = spec.yawDiscreteDeg;
    const deg = arr[Math.floor(rand() * arr.length)]!;
    return (deg * Math.PI) / 180;
  }
  return rand() * Math.PI * 2;
}

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
    | 'yawDistribution'
    | 'yawDiscreteDeg'
    | 'surfaceEpsilon'
    | 'surfaceEpsilonAuto'
    | 'maxDistance'
  >,
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
  const scaleJitter = pickScaleJitter(spec, rand);
  base.scale = [
    parts.scale[0] * scaleJitter,
    parts.scale[1] * scaleJitter,
    parts.scale[2] * scaleJitter,
  ];

  const surface = sampleTerrainSurfaceMatrix(
    state,
    wx,
    wz,
    spec.surfaceEpsilon,
    spec.surfaceEpsilonAuto
  );
  const normal = surface?.normal ?? upNormal;

  const yawRad = pickYawRad(spec, rand);

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

  const urlRaw = template.attributes.url;
  const url = typeof urlRaw === 'string' ? urlRaw.trim() : '';
  const scaleY = Math.max(scaleJitter * parts.scale[1], 1e-6);

  const aabb = getGltfLocalAABB(url);
  const halfWidth = aabb
    ? Math.max(aabb.maxX - aabb.minX, aabb.maxZ - aabb.minZ) / 2
    : 0.5;
  const sink = surface
    ? sinkOffsetForSlope(
        surface.slopeAngleRad,
        halfWidth * scaleJitter,
        spec.alignToTerrain ? surface.slopeAngleRad : 0
      )
    : 0;

  const foot = new THREE.Vector3();
  foot.set(0, 0, 0);
  if (spec.groundAlign === 'aabb' && url) {
    const b = getGltfLocalYBounds(url);
    if (b) {
      const lift = -b.minY * scaleY;
      if (spec.alignToTerrain) {
        foot.copy(normal).multiplyScalar(lift);
      } else {
        foot.set(0, lift, 0);
      }
    } else if (!isGltfBoundsPrefetchInflight(url)) {
      warnMissingGltfBoundsOnce(url);
    }
  }

  base.pos = [
    wx + parts.pos[0] + foot.x,
    wy + parts.pos[1] + spec.baseYOffset + foot.y - sink,
    wz + parts.pos[2] + foot.z,
  ];

  const transformStr = formatTransformAttr(base);
  const attrs = mergeTemplateAttributes(template, transformStr);

  if (template.tagName.toLowerCase() === 'gameobject') {
    delete attrs.place;
    const eid = createEntityFromRecipe(state, 'GameObject', attrs);
    const ch = template.entityChildren;
    if (ch?.length) {
      const context = new ParseContext(state);
      processRecipeChildElements(state, eid, 'GameObject', ch, context);
    }
    if (spec.maxDistance > 0) {
      state.addComponent(eid, DistanceCull);
      DistanceCull.maxDistance[eid] = spec.maxDistance;
    }
    mirrorPoseToRigidbody(state, eid);
    state.addComponent(eid, TerrainSpawned);
    TerrainSpawned.yOffset[eid] = Transform.posY[eid] - wy;
    TerrainSpawned.surfaceEpsilon[eid] = spec.surfaceEpsilon;
    return;
  }

  const eid = createEntityFromRecipe(state, template.tagName, attrs);
  if (spec.maxDistance > 0) {
    state.addComponent(eid, DistanceCull);
    DistanceCull.maxDistance[eid] = spec.maxDistance;
  }
  mirrorPoseToRigidbody(state, eid);
  state.addComponent(eid, TerrainSpawned);
  TerrainSpawned.yOffset[eid] = Transform.posY[eid] - wy;
  TerrainSpawned.surfaceEpsilon[eid] = spec.surfaceEpsilon;
}
