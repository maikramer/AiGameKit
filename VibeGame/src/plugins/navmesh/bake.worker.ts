/**
 * Off-thread solo navmesh bake. Runs in a dedicated Worker so the sync
 * recast WASM rasteriser never stalls the main/gameplay thread.
 *
 * Protocol:
 *   main → worker: { positions: Float32Array, indices: Uint32Array, config }
 *   worker → main: { ok: true, bytes: Uint8Array } | { ok: false, error: string }
 *
 * Positions/indices buffers are transferred (detached on the main side).
 */
import { exportNavMesh, init } from 'recast-navigation';
import { generateSoloNavMesh } from 'recast-navigation/generators';

export type NavMeshBakeConfig = {
  cs: number;
  ch: number;
  walkableSlopeAngle: number;
  walkableHeight: number;
  walkableClimb: number;
  walkableRadius: number;
  maxVertsPerPoly: number;
  detailSampleDist: number;
  detailSampleMaxError: number;
};

export type NavMeshBakeRequest = {
  positions: Float32Array;
  indices: Uint32Array;
  config: NavMeshBakeConfig;
};

export type NavMeshBakeResponse =
  { ok: true; bytes: Uint8Array } | { ok: false; error: string };

self.onmessage = async (event: MessageEvent<NavMeshBakeRequest>) => {
  try {
    await init();
    const { positions, indices, config } = event.data;
    const result = generateSoloNavMesh(positions, indices, config);
    if (!result.success) {
      const response: NavMeshBakeResponse = {
        ok: false,
        error: result.error ?? 'generateSoloNavMesh failed',
      };
      self.postMessage(response);
      return;
    }
    // Copy off the WASM heap before transfer — exportNavMesh may return a
    // view into the emscripten module memory; transferring that buffer would
    // detach the whole heap.
    const bytes = exportNavMesh(result.navMesh).slice();
    result.navMesh.destroy();
    const response: NavMeshBakeResponse = { ok: true, bytes };
    self.postMessage(response, [bytes.buffer]);
  } catch (err) {
    const response: NavMeshBakeResponse = {
      ok: false,
      error: err instanceof Error ? err.message : String(err),
    };
    self.postMessage(response);
  }
};
