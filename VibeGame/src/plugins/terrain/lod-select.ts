/**
 * Quadtree LOD selection — pure-function implementation.
 *
 * Given world parameters and camera position, returns the set of chunk
 * descriptors that should be active. The caller is responsible for
 * spawning / despawning ECS entities to match.
 */

export interface ChunkDesc {
  originX: number;
  originZ: number;
  size: number;
  level: number;
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
 * @param _hysteresis Reserved for future hysteresis support
 * @param camX     Camera world X
 * @param camZ     Camera world world Z
 * @param out      Accumulator for leaf ChunkDescs
 */
function traverse(
  cx: number,
  cz: number,
  size: number,
  level: number,
  maxLevels: number,
  ratio: number,
  _hysteresis: number,
  camX: number,
  camZ: number,
  out: ChunkDesc[],
): void {
  const halfSize = size * 0.5;
  const dx = camX - cx;
  const dz = camZ - cz;
  const dist = Math.sqrt(dx * dx + dz * dz);

  const splitDist = size * ratio;

  if (level < maxLevels - 1 && dist < splitDist) {
    const quarter = halfSize * 0.5;
    traverse(cx - quarter, cz - quarter, halfSize, level + 1, maxLevels, ratio, _hysteresis, camX, camZ, out);
    traverse(cx + quarter, cz - quarter, halfSize, level + 1, maxLevels, ratio, _hysteresis, camX, camZ, out);
    traverse(cx - quarter, cz + quarter, halfSize, level + 1, maxLevels, ratio, _hysteresis, camX, camZ, out);
    traverse(cx + quarter, cz + quarter, halfSize, level + 1, maxLevels, ratio, _hysteresis, camX, camZ, out);
    return;
  }

  // This node is a leaf — it represents a single chunk.
  out.push({
    originX: cx,
    originZ: cz,
    size,
    level,
  });
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
 * @returns Array of ChunkDesc for all active leaf nodes
 */
export function selectChunks(
  worldSize: number,
  levels: number,
  ratio: number,
  hysteresis: number,
  camX: number,
  camZ: number,
): ChunkDesc[] {
  const result: ChunkDesc[] = [];
  traverse(0, 0, worldSize, 0, levels, ratio, hysteresis, camX, camZ, result);
  return result;
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
export function resolutionForLevel(baseResolution: number, level: number): number {
  return Math.max(4, baseResolution >> level);
}
