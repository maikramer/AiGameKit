/**
 * Quadtree LOD selection — pure-function implementation.
 *
 * Given world parameters and camera position, returns the set of chunk
 * descriptors that should be active. The caller is responsible for
 * spawning / despawning ECS entities to match.
 */

import { maxBoostOverAabb, type DensityMap } from './density-map';

export interface ChunkDesc {
  originX: number;
  originZ: number;
  size: number;
  level: number;
}

/**
 * True when density boost on this node still leaves a coarser lattice than the
 * deepest leaf (res is capped at `baseResolution`). Road/river corridors stamp
 * boost 255 — without a forced split, a large far chunk keeps step ≫ bed width
 * and its triangles cut above the carved bed; the transparent road decal then
 * fails the depth test and sand shows through as an orange “fade” band on the
 * chunk edge.
 */
function densityNeedsDeeperSplit(
  density: DensityMap,
  baseResolution: number,
  cx: number,
  cz: number,
  size: number,
  level: number,
  maxLevels: number
): boolean {
  if (level >= maxLevels - 1) return false;
  const half = size * 0.5;
  const boost = maxBoostOverAabb(density, {
    minX: cx - half,
    maxX: cx + half,
    minZ: cz - half,
    maxZ: cz + half,
  });
  if (boost <= 0) return false;
  const resHere = effectiveResolution(baseResolution, level, boost);
  const stepHere = size / Math.max(1, resHere);
  const maxLeafLevel = maxLevels - 1;
  const leafSize = density.worldSize / 2 ** maxLeafLevel;
  const resLeaf = effectiveResolution(baseResolution, maxLeafLevel, boost);
  const stepLeaf = leafSize / Math.max(1, resLeaf);
  return stepHere > stepLeaf * 1.25;
}

/**
 * Recursively traverse a virtual quadtree and collect all leaf nodes
 * that should be rendered this frame.
 *
 * @param cx       Node center X
 * @param cz       Node center Z
 * @param size     Current node size
 * @param level    Current depth (0 = root)
 * @param maxLevels Maximum quadtree depth
 * @param ratio    lodDistanceRatio — split when dist < size * ratio
 * @param hysteresis lodHysteresis — values >1 delay subdivision until the
 *   camera is closer (`splitDist / hysteresis`), reducing LOD thrash at
 *   split boundaries.
 * @param camX     Camera world X
 * @param camZ     Camera world world Z
 * @param out      Accumulator for leaf ChunkDescs
 * @param density  Optional feature density — forces deeper splits when boost
 *   cannot refine the lattice without subdividing
 * @param baseResolution Terrain.resolution (needed for density split check)
 */
function traverse(
  cx: number,
  cz: number,
  size: number,
  level: number,
  maxLevels: number,
  ratio: number,
  hysteresis: number,
  camX: number,
  camZ: number,
  out: ChunkDesc[],
  density?: DensityMap,
  baseResolution?: number
): void {
  const halfSize = size * 0.5;
  const dx = camX - cx;
  const dz = camZ - cz;
  const dist = Math.sqrt(dx * dx + dz * dz);

  // hysteresis > 1: require camera closer before subdividing. Cuts boundary
  // thrash (parent ↔ 4 children) and keeps more coarse leaves on average.
  const hyst = Math.max(hysteresis, 1);
  const splitDist = (size * ratio) / hyst;

  const forceDensitySplit =
    !!density &&
    !!baseResolution &&
    densityNeedsDeeperSplit(
      density,
      baseResolution,
      cx,
      cz,
      size,
      level,
      maxLevels
    );

  if (level < maxLevels - 1 && (dist < splitDist || forceDensitySplit)) {
    const quarter = halfSize * 0.5;
    traverse(
      cx - quarter,
      cz - quarter,
      halfSize,
      level + 1,
      maxLevels,
      ratio,
      hysteresis,
      camX,
      camZ,
      out,
      density,
      baseResolution
    );
    traverse(
      cx + quarter,
      cz - quarter,
      halfSize,
      level + 1,
      maxLevels,
      ratio,
      hysteresis,
      camX,
      camZ,
      out,
      density,
      baseResolution
    );
    traverse(
      cx - quarter,
      cz + quarter,
      halfSize,
      level + 1,
      maxLevels,
      ratio,
      hysteresis,
      camX,
      camZ,
      out,
      density,
      baseResolution
    );
    traverse(
      cx + quarter,
      cz + quarter,
      halfSize,
      level + 1,
      maxLevels,
      ratio,
      hysteresis,
      camX,
      camZ,
      out,
      density,
      baseResolution
    );
    return;
  }

  // This node is a leaf — reuse pooled ChunkDesc to avoid per-frame alloc.
  const desc = acquireChunkDesc();
  desc.originX = cx;
  desc.originZ = cz;
  desc.size = size;
  desc.level = level;
  out.push(desc);
}

/** Pooled leaf descriptors + result array (valid until next selectChunks). */
const _chunkDescPool: ChunkDesc[] = [];
let _chunkDescPoolUsed = 0;
const _selectResult: ChunkDesc[] = [];

function acquireChunkDesc(): ChunkDesc {
  let d = _chunkDescPool[_chunkDescPoolUsed];
  if (!d) {
    d = { originX: 0, originZ: 0, size: 0, level: 0 };
    _chunkDescPool[_chunkDescPoolUsed] = d;
  }
  _chunkDescPoolUsed++;
  return d;
}

/**
 * Select the set of terrain chunks that should be visible for the given
 * camera position.
 *
 * @param worldSize  Total world size (e.g. 256)
 * @param levels     Number of LOD levels (e.g. 6)
 * @param ratio      lodDistanceRatio
 * @param hysteresis lodHysteresis
 * @param camX       Camera X in world space
 * @param camZ       Camera Z in world space
 * @param density    Optional feature density map (road/river/pad stamps)
 * @param baseResolution Terrain.resolution — with `density`, forces splits
 *   until the lattice step matches the deepest leaf under boosted regions
 * @returns Array of ChunkDesc for all active leaf nodes (scratch — do not
 *   retain across calls)
 */
export function selectChunks(
  worldSize: number,
  levels: number,
  ratio: number,
  hysteresis: number,
  camX: number,
  camZ: number,
  density?: DensityMap,
  baseResolution?: number
): ChunkDesc[] {
  _chunkDescPoolUsed = 0;
  _selectResult.length = 0;
  traverse(
    0,
    0,
    worldSize,
    0,
    levels,
    ratio,
    hysteresis,
    camX,
    camZ,
    _selectResult,
    density,
    baseResolution
  );
  return _selectResult;
}

/**
 * Build a stable string key for a chunk descriptor, used to match
 * existing chunk entities with desired chunks.
 */
export function chunkKey(desc: ChunkDesc): string {
  return `${desc.originX},${desc.originZ},${desc.level}`;
}

/**
 * Compute the mesh resolution for a given LOD level.
 * Halves per level with a floor of 4.
 */
export function resolutionForLevel(
  baseResolution: number,
  level: number
): number {
  return Math.max(4, baseResolution >> level);
}

/**
 * Effective mesh resolution for a chunk, layering a spatial density boost on
 * top of the camera-LOD resolution. boost=0 (no featured region) reproduces
 * {@link resolutionForLevel} exactly — the retrocompat contract. boost=255
 * multiplies the LOD resolution by 8, capped at `baseResolution`.
 *
 * Why ×8: the uniform lattice spacing is `worldSize/baseResolution` (~15 m in
 * simple-rpg) at EVERY level (size and resolution halve together). Carved
 * features narrower than the lattice — river channels+banks (~2 m features),
 * road corridors (~10 m) — live in the sampler but the mesh never sampled
 * them: rivers rendered with no depth or banks, roads looked buried. The old
 * ×2 ceiling still left a 7.8 m lattice. ×8 → ~2 m over boosted tiles only,
 * which is what banks/corridor shoulders need to actually show up.
 *
 * Intermediate boost scales linearly: factor = 1 + 7·boost/255.
 *
 * @param baseResolution Terrain.resolution (e.g. 64)
 * @param level          LOD quadtree depth (0 = root)
 * @param densityBoost   0..255 from the DensityMap tiles the chunk overlaps
 */
export function effectiveResolution(
  baseResolution: number,
  level: number,
  densityBoost: number
): number {
  const lodRes = resolutionForLevel(baseResolution, level);
  if (densityBoost <= 0) return lodRes;
  const factor = 1 + (Math.min(densityBoost, 255) / 255) * 7;
  const boosted = Math.round(lodRes * factor);
  return Math.min(baseResolution, Math.max(lodRes, boosted));
}

/**
 * Field-local AABB of the deepest LOD leaf that contains `(localX, localZ)`.
 *
 * Matches `selectChunks` partition: root centred at 0 with `worldSize`,
 * deepest leaf level = `levels - 1` (traverse splits while `level < levels - 1`).
 */
export function deepestLeafAabb(
  worldSize: number,
  levels: number,
  localX: number,
  localZ: number
): { minX: number; maxX: number; minZ: number; maxZ: number } {
  const maxLeafLevel = Math.max(0, Math.floor(levels) - 1);
  const tiles = 2 ** maxLeafLevel;
  const size = worldSize / tiles;
  const half = worldSize / 2;
  const ix = Math.min(
    tiles - 1,
    Math.max(0, Math.floor((localX + half) / size))
  );
  const iz = Math.min(
    tiles - 1,
    Math.max(0, Math.floor((localZ + half) / size))
  );
  const minX = -half + ix * size;
  const minZ = -half + iz * size;
  return { minX, maxX: minX + size, minZ, maxZ: minZ + size };
}

/**
 * Lattice resolution for spawn/ground sampling that matches the *rendered*
 * mesh near featured regions (density boost).
 *
 * Chunks pick resolution via {@link maxBoostOverAabb} over the whole leaf.
 * Sampling only `boostAt(point)` left props on the coarse lattice when the
 * point sat on a quiet tile next to a featured neighbour inside the same
 * leaf — classic “few floating trees” on dune / pad-skirt variance maps.
 *
 * `sampleMeshSurfaceHeight` uses `step = worldSize / resolution`. A boosted
 * leaf renders at `step = leafSize / effectiveResolution(...)`. This helper
 * returns the equivalent world lattice resolution.
 */
export function meshSurfaceResolutionForPoint(
  baseResolution: number,
  levels: number,
  density: DensityMap | undefined,
  localX: number,
  localZ: number
): number {
  const base = Math.max(1, Math.floor(baseResolution) || 1);
  if (!density) return base;
  const maxLeafLevel = Math.max(0, Math.floor(levels) - 1);
  const leaf = deepestLeafAabb(density.worldSize, levels, localX, localZ);
  const boost = maxBoostOverAabb(density, leaf);
  if (boost <= 0) return base;
  const leafRes = effectiveResolution(base, maxLeafLevel, boost);
  // Equate leaf step to sampleMeshSurfaceHeight's worldSize/res step:
  // res = leafRes * 2^maxLeafLevel.
  const equiv = leafRes * 2 ** maxLeafLevel;
  return Math.max(base, Math.floor(equiv));
}
