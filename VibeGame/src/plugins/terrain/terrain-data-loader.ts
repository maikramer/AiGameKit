export interface TerrainData {
  version: string;
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
  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    throw new Error(`Failed to fetch terrain data from ${url}: ${(err as Error).message}`);
  }

  if (!response.ok) {
    throw new Error(`Failed to fetch terrain data from ${url}: HTTP ${response.status}`);
  }

  let json: unknown;
  try {
    json = await response.json();
  } catch (err) {
    throw new Error(`Invalid JSON in terrain data from ${url}: ${(err as Error).message}`);
  }

  return parseTerrainData(json);
}

export function parseTerrainData(data: unknown): TerrainData {
  if (!data || typeof data !== "object") {
    throw new Error("Terrain data must be a non-null object");
  }

  const root = data as Record<string, unknown>;

  if (typeof root.version !== "string") {
    throw new Error('Terrain data missing required field: "version" (string)');
  }

  if (!root.terrain || typeof root.terrain !== "object") {
    throw new Error('Terrain data missing required field: "terrain" (object)');
  }

  const terrain = root.terrain as Record<string, unknown>;
  if (typeof terrain.size !== "number") {
    throw new Error('Terrain data missing required field: "terrain.size" (number)');
  }
  if (typeof terrain.world_size !== "number") {
    throw new Error('Terrain data missing required field: "terrain.world_size" (number)');
  }
  if (typeof terrain.max_height !== "number") {
    throw new Error('Terrain data missing required field: "terrain.max_height" (number)');
  }

  const rivers = Array.isArray(root.rivers) ? root.rivers : [];
  const lakes = Array.isArray(root.lakes) ? root.lakes : [];
  const lakePlanes = Array.isArray(root.lake_planes) ? root.lake_planes : [];

  return {
    version: root.version,
    terrain: {
      size: terrain.size,
      world_size: terrain.world_size,
      max_height: terrain.max_height,
      height_min: typeof terrain.height_min === "number" ? terrain.height_min : undefined,
      height_max: typeof terrain.height_max === "number" ? terrain.height_max : undefined,
      height_mean: typeof terrain.height_mean === "number" ? terrain.height_mean : undefined,
    },
    rivers,
    lakes,
    lake_planes: lakePlanes,
  };
}

export function spawnWaterEntitiesFromTerrainData(terrainData: TerrainData): void {
  for (const plane of terrainData.lake_planes) {
    console.log(
      `[terrain-data-loader] Would spawn <Water> entity: pos="${plane.pos_x} ${plane.pos_y} ${plane.pos_z}" size-x="${plane.size_x}" size-z="${plane.size_z}"> (lake_id=${plane.lake_id})`
    );
  }

  for (const river of terrainData.rivers) {
    console.log(
      `[terrain-data-loader] Would spawn water segments for river id=${river.id} (${river.path.length} points, length=${river.length})`
    );
  }
}
