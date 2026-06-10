import * as THREE from 'three';
import type { State } from '../../core';
import { Terrain } from '../terrain/components';
import { sampleHeightAt } from '../terrain/height-sampler';
import { getTerrainContext } from '../terrain/utils';
import { registerBvhMesh, unregisterBvhMesh } from './utils';

/**
 * Build a single tessellated PlaneGeometry for a terrain entity with vertex
 * heights sampled from the heightmap. Vertices are world-space.
 *
 * Resolution is `gridDivisions` × `gridDivisions` quads (= (gridDivisions+1)^2
 * vertices). 256 gives a great quality/memory balance on a 10 km terrain (~131k
 * tris, BVH built in ~50ms).
 *
 * No normal attribute: the BVH raycast derives face normals geometrically and
 * the mesh is never rendered, so vertex normals would be dead weight.
 */
function buildTerrainBvhGeometry(
  worldOffset: { x: number; y: number; z: number },
  gridDivisions: number,
  worldSize: number,
  sampler: (x: number, z: number) => number
): THREE.BufferGeometry {
  const segments = gridDivisions;
  const verts = segments + 1;
  const positions = new Float32Array(verts * verts * 3);
  const indices = new Uint32Array(segments * segments * 6);

  const half = worldSize / 2;
  const step = worldSize / segments;

  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      const wx = -half + x * step + worldOffset.x;
      const wz = -half + z * step + worldOffset.z;
      const wy = sampler(wx, wz) + worldOffset.y;
      const i = (z * verts + x) * 3;
      positions[i] = wx;
      positions[i + 1] = wy;
      positions[i + 2] = wz;
    }
  }

  let t = 0;
  for (let z = 0; z < segments; z++) {
    for (let x = 0; x < segments; x++) {
      const a = z * verts + x;
      const b = a + 1;
      const c = a + verts;
      const d = c + 1;
      indices[t++] = a;
      indices[t++] = c;
      indices[t++] = b;
      indices[t++] = b;
      indices[t++] = c;
      indices[t++] = d;
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  geo.setIndex(new THREE.BufferAttribute(indices, 1));
  return geo;
}

interface BuiltTerrain {
  key: string;
  /** Terrain context object the geometry was built from; identity change means
   * the entity id was recycled (or the terrain was recreated) → rebuild. */
  data: object;
}

const builtKeys = new WeakMap<State, Map<number, BuiltTerrain>>();

function getBuiltKeys(state: State): Map<number, BuiltTerrain> {
  let m = builtKeys.get(state);
  if (!m) {
    m = new Map();
    builtKeys.set(state, m);
  }
  return m;
}

/**
 * For every terrain entity that finished `init()` and has not yet been added
 * to the BVH, generate a displaced plane geometry and register it.
 *
 * Resolution defaults to 256 segments per terrain (≈ 131k triangles, ~120 KB
 * for a 10 km map). Heightmap is sampled via the terrain plugin's bilinear
 * helper for smooth queries that match what the player sees.
 */
export function syncTerrainBvh(
  state: State,
  gridDivisions = 256
): { added: number; total: number } {
  const built = getBuiltKeys(state);
  const terrainCtx = getTerrainContext(state);
  let added = 0;

  for (const [entity, data] of terrainCtx) {
    if (!data.initialized) continue;
    const prev = built.get(entity);
    if (prev) {
      if (prev.data === data) continue;
      // Entity id recycled into a different terrain: drop the stale mesh.
      unregisterBvhMesh(state, prev.key);
      built.delete(entity);
    }

    const worldSize = Terrain.worldSize[entity];
    const sampler = (x: number, z: number) => {
      const lx = x - data.worldOffset.x;
      const lz = z - data.worldOffset.z;
      return sampleHeightAt(data.sampler, lx, lz);
    };

    const geometry = buildTerrainBvhGeometry(
      data.worldOffset,
      gridDivisions,
      worldSize,
      sampler
    );

    const key = `terrain:${entity}`;
    registerBvhMesh(state, key, geometry, {
      entity,
      layer: 0x0001,
    });
    built.set(entity, { key, data });
    added++;
  }

  for (const [entity, info] of [...built]) {
    if (!state.exists(entity) || !terrainCtx.has(entity)) {
      unregisterBvhMesh(state, info.key);
      built.delete(entity);
    }
  }

  return { added, total: built.size };
}

/** Force rebuild on next sync (e.g. after async heightmap load replaces sampler). */
export function invalidateTerrainBvh(state: State, entity: number): void {
  const built = getBuiltKeys(state);
  const info = built.get(entity);
  if (info !== undefined) {
    unregisterBvhMesh(state, info.key);
    built.delete(entity);
  }
}
