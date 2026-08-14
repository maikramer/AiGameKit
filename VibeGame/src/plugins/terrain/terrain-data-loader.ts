import { fetchJsonResilient } from '../../core/utils/resilient-net';
import { createEntityFromRecipe } from '../../core/recipes/parser';
import { setRiverPath } from '../water/components';

export interface TerrainData {
  version: string;
  /**
   * On-disk heightmap encoding. `png` (default) keeps the legacy uint8
   * luminance decode; `ahgt` selects the uint16+deflate binary format
   * (see terrain/ahgt-format.ts) for ~3mm precision over 200m. Optional in
   * hand-built literals; parseTerrainData always fills it in.
   */
  heightmap_format?: 'png' | 'ahgt';
  terrain: {
    size: number;
    world_size: number;
    max_height: number;
    height_min?: number;
    height_max?: number;
    height_mean?: number;
  };
  rivers: Array<{
    id: number;
    source: [number, number];
    path: Array<[number, number]>;
    length: number;
  }>;
  lakes: Array<{
    id: number;
    center_pixel: [number, number];
    surface_level: number;
    surface_height: number;
    area_pixels: number;
    depth?: number;
  }>;
  lake_planes: Array<{
    lake_id: number;
    pos_x: number;
    pos_y: number;
    pos_z: number;
    size_x: number;
    size_z: number;
  }>;
}

export async function loadTerrainData(url: string): Promise<TerrainData> {
  let json: unknown;
  try {
    json = await fetchJsonResilient(url);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`Failed to fetch terrain data from ${url}: ${detail}`, {
      cause: err,
    });
  }
  return parseTerrainData(json);
}

// Hostile-input guards: a crafted or truncated terrain JSON must fail loudly
// at parse time, never leak NaN/Infinity into geometry or memory bombs into
// the sampler.
const MAX_WATER_ENTITIES = 20_000;

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value)
    ? value
    : undefined;
}

function finitePoint(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null;
  const x = finiteNumber(value[0]);
  const z = finiteNumber(value[1]);
  return x === undefined || z === undefined ? null : [x, z];
}

function sanitizeRivers(raw: unknown): TerrainData['rivers'] {
  if (!Array.isArray(raw)) return [];
  const rivers: TerrainData['rivers'] = [];
  for (const item of raw) {
    if (rivers.length >= MAX_WATER_ENTITIES) break;
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const path: Array<[number, number]> = [];
    if (Array.isArray(row.path)) {
      for (const point of row.path) {
        const p = finitePoint(point);
        if (p) path.push(p);
      }
    }
    if (path.length < 2) continue;
    rivers.push({
      id: finiteNumber(row.id) ?? rivers.length,
      source: finitePoint(row.source) ?? path[0]!,
      path,
      length: finiteNumber(row.length) ?? path.length,
    });
  }
  return rivers;
}

function sanitizeLakes(raw: unknown): TerrainData['lakes'] {
  if (!Array.isArray(raw)) return [];
  const lakes: TerrainData['lakes'] = [];
  for (const item of raw) {
    if (lakes.length >= MAX_WATER_ENTITIES) break;
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const center = finitePoint(row.center_pixel);
    const surfaceLevel = finiteNumber(row.surface_level);
    const surfaceHeight = finiteNumber(row.surface_height);
    const areaPixels = finiteNumber(row.area_pixels);
    if (!center || surfaceLevel === undefined || surfaceHeight === undefined) {
      continue;
    }
    const depth = finiteNumber(row.depth);
    lakes.push({
      id: finiteNumber(row.id) ?? lakes.length,
      center_pixel: center,
      surface_level: surfaceLevel,
      surface_height: surfaceHeight,
      area_pixels: areaPixels ?? 0,
      ...(depth !== undefined ? { depth } : {}),
    });
  }
  return lakes;
}

function sanitizeLakePlanes(
  raw: unknown,
  lakeIds: Set<number>
): TerrainData['lake_planes'] {
  if (!Array.isArray(raw)) return [];
  const planes: TerrainData['lake_planes'] = [];
  for (const item of raw) {
    if (planes.length >= MAX_WATER_ENTITIES) break;
    if (!item || typeof item !== 'object') continue;
    const row = item as Record<string, unknown>;
    const lakeId = finiteNumber(row.lake_id);
    const posX = finiteNumber(row.pos_x);
    const posY = finiteNumber(row.pos_y);
    const posZ = finiteNumber(row.pos_z);
    const sizeX = finiteNumber(row.size_x);
    const sizeZ = finiteNumber(row.size_z);
    if (
      lakeId === undefined ||
      !lakeIds.has(lakeId) ||
      posX === undefined ||
      posY === undefined ||
      posZ === undefined ||
      sizeX === undefined ||
      sizeZ === undefined ||
      sizeX <= 0 ||
      sizeZ <= 0
    ) {
      continue;
    }
    planes.push({
      lake_id: lakeId,
      pos_x: posX,
      pos_y: posY,
      pos_z: posZ,
      size_x: sizeX,
      size_z: sizeZ,
    });
  }
  return planes;
}

export function parseTerrainData(data: unknown): TerrainData {
  if (!data || typeof data !== 'object') {
    throw new Error('Terrain data must be a non-null object');
  }

  const root = data as Record<string, unknown>;

  if (typeof root.version !== 'string') {
    throw new Error('Terrain data missing required field: "version" (string)');
  }

  if (!root.terrain || typeof root.terrain !== 'object') {
    throw new Error('Terrain data missing required field: "terrain" (object)');
  }

  const terrain = root.terrain as Record<string, unknown>;
  const size = terrain.size;
  const worldSize = terrain.world_size;
  const maxHeight = terrain.max_height;
  if (
    typeof size !== 'number' ||
    !Number.isInteger(size) ||
    size < 1 ||
    size > 65_536
  ) {
    throw new Error(
      'Terrain data field "terrain.size" must be an integer in [1, 65536]'
    );
  }
  if (
    typeof worldSize !== 'number' ||
    !Number.isFinite(worldSize) ||
    worldSize <= 0
  ) {
    throw new Error(
      'Terrain data field "terrain.world_size" must be a finite positive number'
    );
  }
  if (
    typeof maxHeight !== 'number' ||
    !Number.isFinite(maxHeight) ||
    maxHeight < 0
  ) {
    throw new Error(
      'Terrain data field "terrain.max_height" must be a finite non-negative number'
    );
  }

  const rivers = sanitizeRivers(root.rivers);
  const lakes = sanitizeLakes(root.lakes);
  const lakePlanes = sanitizeLakePlanes(
    root.lake_planes,
    new Set(lakes.map((lake) => lake.id))
  );

  return {
    version: root.version,
    heightmap_format: root.heightmap_format === 'ahgt' ? 'ahgt' : 'png',
    terrain: {
      size,
      world_size: worldSize,
      max_height: maxHeight,
      height_min: finiteNumber(terrain.height_min),
      height_max: finiteNumber(terrain.height_max),
      height_mean: finiteNumber(terrain.height_mean),
    },
    rivers,
    lakes,
    lake_planes: lakePlanes,
  };
}

export function spawnWaterEntitiesFromTerrainData(
  state: import('../../core').State,
  terrainData: TerrainData
): void {
  const { size, world_size } = terrainData.terrain;
  // pixel → world: world = (pixel / size) * world_size - world_size/2.
  const toWorld = (px: number): number =>
    (px / size) * world_size - world_size / 2;
  for (const river of terrainData.rivers) {
    if (!river.path || river.path.length < 2) continue;
    const flat: number[] = [];
    for (const [px, pz] of river.path) {
      flat.push(toWorld(px), toWorld(pz));
    }
    const eid = createEntityFromRecipe(state, 'River', {});
    if (eid >= 0) {
      setRiverPath(state, eid, flat);
    }
  }
}
