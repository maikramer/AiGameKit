import { logger } from '../../core/utils/logger';
import type { Parser, State } from '../../core';
import { BiomeRegion } from './components';
import {
  ambientAdapter,
  bgmLayerAdapter,
  fogColorAdapter,
  fogDensityAdapter,
  parsePolygonString,
  pointInPolygon,
  polygonAdapter,
  tintAdapter,
  typeAdapter,
  aabbContains,
} from './adapters';

/**
 * Runtime record for one registered biome region. The full polygon vertex list
 * (variable-length, cannot live in SOA) is kept here for narrow-phase tests.
 */
export interface BiomeRegionInfo {
  entity: number;
  id: string;
  vertices: number[][];
  minX: number;
  minZ: number;
  maxX: number;
  maxZ: number;
  terrainTexture?: string;
}

const REGIONS = new WeakMap<State, BiomeRegionInfo[]>();

export function getBiomeRegions(state: State): BiomeRegionInfo[] {
  let list = REGIONS.get(state);
  if (!list) {
    list = [];
    REGIONS.set(state, list);
  }
  return list;
}

export function findBiomeRegionAt(
  state: State,
  x: number,
  z: number
): BiomeRegionInfo | null {
  const regions = REGIONS.get(state);
  if (!regions || regions.length === 0) return null;
  for (const r of regions) {
    if (!aabbContains(r.minX, r.minZ, r.maxX, r.maxZ, x, z)) continue;
    if (pointInPolygon(x, z, r.vertices)) return r;
  }
  return null;
}

function attr(
  attributes: Record<string, unknown>,
  key: string
): string | undefined {
  const v = attributes[key];
  if (v === undefined || v === null) return undefined;
  return String(v);
}

export const biomeRegionParser: Parser = ({ entity, element, state }) => {
  const a = element.attributes as Record<string, unknown>;

  BiomeRegion.type[entity] = 0;
  BiomeRegion.fogDensity[entity] = 0;
  BiomeRegion.bgmLayer[entity] = 0;
  BiomeRegion.fogColor[entity] = 0;
  BiomeRegion.tintR[entity] = 1;
  BiomeRegion.tintG[entity] = 1;
  BiomeRegion.tintB[entity] = 1;
  BiomeRegion.ambientR[entity] = 1;
  BiomeRegion.ambientG[entity] = 1;
  BiomeRegion.ambientB[entity] = 1;

  const typeRaw = attr(a, 'type');
  if (typeRaw !== undefined) typeAdapter(entity, typeRaw, state);

  const polygonRaw = attr(a, 'polygon');
  if (polygonRaw !== undefined) polygonAdapter(entity, polygonRaw, state);

  const tintRaw = attr(a, 'tint');
  if (tintRaw !== undefined) tintAdapter(entity, tintRaw, state);

  const fogColorRaw = attr(a, 'fog-color');
  if (fogColorRaw !== undefined) fogColorAdapter(entity, fogColorRaw, state);

  const fogDensityRaw = attr(a, 'fog-density');
  if (fogDensityRaw !== undefined)
    fogDensityAdapter(entity, fogDensityRaw, state);

  const ambientRaw = attr(a, 'ambient');
  if (ambientRaw !== undefined) ambientAdapter(entity, ambientRaw, state);

  const bgmRaw = attr(a, 'bgm-layer');
  if (bgmRaw !== undefined) bgmLayerAdapter(entity, bgmRaw, state);

  const terrainTextureRaw = attr(a, 'terrain-texture');

  const id = attr(a, 'id') ?? `biome-${entity}`;
  const geometry =
    polygonRaw !== undefined
      ? parsePolygonString(polygonRaw)
      : parsePolygonString('');

  const info: BiomeRegionInfo = {
    entity,
    id,
    vertices: geometry.vertices,
    minX: BiomeRegion.polyMinX[entity],
    minZ: BiomeRegion.polyMinZ[entity],
    maxX: BiomeRegion.polyMaxX[entity],
    maxZ: BiomeRegion.polyMaxZ[entity],
    terrainTexture: terrainTextureRaw || undefined,
  };

  if (info.vertices.length < 3) {
    logger.warn(
      `[biomes] Region "%s" has fewer than 3 polygon vertices — it will never match`,
      id
    );
  }

  getBiomeRegions(state).push(info);
};
