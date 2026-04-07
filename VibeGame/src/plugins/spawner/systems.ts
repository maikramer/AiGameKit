import * as THREE from 'three';
import {
  defineQuery,
  type State,
  type System,
  type XMLValue,
} from '../../core';
import { createEntityFromRecipe } from '../../core/recipes/parser';
import { SpawnerPending } from './components';
import { getSpawnGroupSpecs } from './context';
import {
  isNormalWithinSlopeLimit,
  sampleTerrainSurface,
  type TerrainSurfaceSample,
} from './surface';
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import {
  composeSpawnRotation,
  defaultTransformParts,
  formatTransformAttr,
  parseTransformAttr,
} from './transform-merge';
import {
  getGltfLocalYBounds,
  warnMissingGltfBoundsOnce,
} from '../gltf-xml/gltf-bounds-cache';
import { TransformHierarchySystem } from '../transforms';
import { Transform, WorldTransform } from '../transforms/components';

const upNormal = new THREE.Vector3(0, 1, 0);
const _foot = new THREE.Vector3();

const spawnerQuery = defineQuery([SpawnerPending]);

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a += 0x6d2b79f5;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function anchorOffset(
  state: State,
  spawnerEid: number
): [number, number, number] {
  if (state.hasComponent(spawnerEid, WorldTransform)) {
    return [
      WorldTransform.posX[spawnerEid],
      WorldTransform.posY[spawnerEid],
      WorldTransform.posZ[spawnerEid],
    ];
  }
  return [
    Transform.posX[spawnerEid],
    Transform.posY[spawnerEid],
    Transform.posZ[spawnerEid],
  ];
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

function spawnOne(
  state: State,
  spec: SpawnGroupSpec,
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
  const scaleJitter = spec.scaleMin + rand() * (spec.scaleMax - spec.scaleMin);
  base.scale = [
    parts.scale[0] * scaleJitter,
    parts.scale[1] * scaleJitter,
    parts.scale[2] * scaleJitter,
  ];

  const surface = sampleTerrainSurface(state, wx, wz, spec.surfaceEpsilon);
  const normal = surface?.normal ?? upNormal;

  let yawRad = 0;
  if (spec.randomYaw) {
    yawRad = rand() * Math.PI * 2;
  }

  const euler = composeSpawnRotation(
    normal,
    spec.alignToTerrain,
    yawRad,
    parts.euler
  );
  base.euler = [euler.x, euler.y, euler.z];

  const urlRaw = template.attributes.url;
  const url = typeof urlRaw === 'string' ? urlRaw.trim() : '';
  const scaleY = Math.max(scaleJitter * parts.scale[1], 1e-6);

  _foot.set(0, 0, 0);
  if (spec.groundAlign === 'aabb' && url) {
    const b = getGltfLocalYBounds(url);
    if (b) {
      const lift = -b.minY * scaleY;
      if (spec.alignToTerrain) {
        _foot.copy(normal).multiplyScalar(lift);
      } else {
        _foot.set(0, lift, 0);
      }
    } else {
      warnMissingGltfBoundsOnce(url);
    }
  }

  base.pos = [
    wx + parts.pos[0] + _foot.x,
    wy + parts.pos[1] + spec.baseYOffset + _foot.y,
    wz + parts.pos[2] + _foot.z,
  ];

  const transformStr = formatTransformAttr(base);
  const attrs = mergeTemplateAttributes(template, transformStr);
  createEntityFromRecipe(state, template.tagName, attrs);
}

export const TerrainSpawnSystem: System = {
  group: 'simulation',
  after: [TransformHierarchySystem],
  update(state) {
    if (state.headless) return;

    const specs = getSpawnGroupSpecs(state);
    if (specs.size === 0) return;

    for (const eid of spawnerQuery(state.world)) {
      if (SpawnerPending.spawned[eid]) continue;

      const spec = specs.get(eid);
      if (!spec) {
        SpawnerPending.spawned[eid] = 1;
        continue;
      }

      const surfaceProbe = sampleTerrainSurface(
        state,
        0,
        0,
        spec.surfaceEpsilon
      );
      if (!surfaceProbe) continue;

      const rand = mulberry32(spec.seed >>> 0);
      const [ax, , az] = anchorOffset(state, eid);
      const minX = spec.regionMin[0] + ax;
      const maxX = spec.regionMax[0] + ax;
      const minZ = spec.regionMin[2] + az;
      const maxZ = spec.regionMax[2] + az;

      const maxSlope = Number.isFinite(spec.maxSlopeDeg)
        ? spec.maxSlopeDeg
        : 45;
      const acceptAnySlope = maxSlope >= 90 - 1e-6;

      for (let i = 0; i < spec.count; i++) {
        let wx = minX;
        let wz = minZ;
        let s: TerrainSurfaceSample | null = null;
        let foundValidSlope = false;
        const attempts = Math.max(1, spec.maxSlopePlacementAttempts);
        for (let attempt = 0; attempt < attempts; attempt++) {
          wx = minX + rand() * (maxX - minX);
          wz = minZ + rand() * (maxZ - minZ);
          const cand = sampleTerrainSurface(state, wx, wz, spec.surfaceEpsilon);
          if (!cand) continue;
          s = cand;
          if (isNormalWithinSlopeLimit(cand.normal, maxSlope)) {
            foundValidSlope = true;
            break;
          }
        }

        if (!s) continue;
        if (!foundValidSlope && !acceptAnySlope) {
          continue;
        }

        const wy = s.worldY;

        let template: SpawnTemplateSpec;
        if (spec.pickStrategy === 'round-robin') {
          template = spec.templates[i % spec.templates.length]!;
        } else {
          template =
            spec.templates[Math.floor(rand() * spec.templates.length)]!;
        }

        spawnOne(state, spec, rand, wx, wy, wz, template);
      }

      SpawnerPending.spawned[eid] = 1;
    }
  },
};
