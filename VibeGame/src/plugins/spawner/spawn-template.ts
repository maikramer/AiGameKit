import * as THREE from 'three';
import type { State, XMLValue } from '../../core';
import { ParseContext } from '../../core/recipes/parse-context';
import {
  createEntityFromRecipe,
  processRecipeChildElements,
} from '../../core/recipes/parser';
import { sampleTerrainSurface } from './surface';
import type { SpawnGroupSpec, SpawnTemplateSpec } from './types';
import {
  composeSpawnRotation,
  defaultTransformParts,
  formatTransformAttr,
  parseTransformAttr,
} from './transform-merge';
import {
  getGltfLocalYBounds,
  isGltfBoundsPrefetchInflight,
  warnMissingGltfBoundsOnce,
} from '../gltf-xml/gltf-bounds-cache';

const upNormal = new THREE.Vector3(0, 1, 0);
const _foot = new THREE.Vector3();

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
export function spawnTemplateAtTerrain(
  state: State,
  spec: Pick<
    SpawnGroupSpec,
    | 'alignToTerrain'
    | 'baseYOffset'
    | 'groundAlign'
    | 'randomYaw'
    | 'scaleMin'
    | 'scaleMax'
    | 'surfaceEpsilon'
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
    } else if (!isGltfBoundsPrefetchInflight(url)) {
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

  if (template.tagName.toLowerCase() === 'gameobject') {
    delete attrs.place;
    const root = createEntityFromRecipe(state, 'GameObject', attrs);
    const ch = template.entityChildren;
    if (ch?.length) {
      const context = new ParseContext(state);
      processRecipeChildElements(state, root, 'GameObject', ch, context);
    }
    return;
  }

  createEntityFromRecipe(state, template.tagName, attrs);
}
