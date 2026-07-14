import { beforeAll, describe, expect, it } from 'bun:test';
import { init, importNavMesh } from 'recast-navigation';
import {
  bakeSoloNavMeshBytes,
  type NavMeshBakeConfig,
  prefetchNavmeshObstacles,
  State,
} from 'vibegame';

const FLAT_CONFIG: NavMeshBakeConfig = {
  cs: 0.5,
  ch: 0.5,
  walkableSlopeAngle: 45,
  walkableHeight: 4,
  walkableClimb: 1,
  walkableRadius: 1,
  maxVertsPerPoly: 6,
  detailSampleDist: 3,
  detailSampleMaxError: 0.5,
};

/** Larger flat grid — tiny quads often fail Detour at cs≥0.5. */
function flatGrid(
  half = 8,
  divisions = 8
): { positions: Float32Array; indices: Uint32Array } {
  const verts = divisions + 1;
  const step = (half * 2) / divisions;
  const positions = new Float32Array(verts * verts * 3);
  const indices = new Uint32Array(divisions * divisions * 6);
  let pi = 0;
  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      positions[pi++] = -half + x * step;
      positions[pi++] = 0;
      positions[pi++] = -half + z * step;
    }
  }
  let ti = 0;
  for (let z = 0; z < divisions; z++) {
    for (let x = 0; x < divisions; x++) {
      const a = z * verts + x;
      const b = a + 1;
      const c = a + verts;
      const d = c + 1;
      indices[ti++] = a;
      indices[ti++] = c;
      indices[ti++] = b;
      indices[ti++] = b;
      indices[ti++] = c;
      indices[ti++] = d;
    }
  }
  return { positions, indices };
}

describe('NavMesh bake worker helpers', () => {
  beforeAll(async () => {
    await init();
  });

  it('prefetchNavmeshObstacles is a no-op on an empty world', () => {
    const state = new State();
    expect(() => prefetchNavmeshObstacles(state, 120)).not.toThrow();
  });

  it('bakeSoloNavMeshBytes produces importable navmesh bytes (sync/worker)', async () => {
    const { positions, indices } = flatGrid();
    const bytes = await bakeSoloNavMeshBytes(positions, indices, FLAT_CONFIG);
    expect(bytes).toBeInstanceOf(Uint8Array);
    expect(bytes.byteLength).toBeGreaterThan(0);

    const { navMesh } = importNavMesh(bytes);
    expect(navMesh).toBeDefined();
    navMesh.destroy();
  }, 30_000);
});
