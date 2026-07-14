import { generateSoloNavMesh } from 'recast-navigation/generators';
import { exportNavMesh } from 'recast-navigation';
import type {
  NavMeshBakeConfig,
  NavMeshBakeRequest,
  NavMeshBakeResponse,
} from './bake.worker';

export type { NavMeshBakeConfig };

/**
 * Bake a solo navmesh off the main thread when Workers are available.
 * Falls back to an inline sync bake (same algorithm) when Workers are missing
 * or fail to start — keeps headless/tests and older browsers working.
 *
 * `positions` / `indices` buffers may be transferred to the worker (detached
 * afterwards); callers must not reuse them.
 */
export async function bakeSoloNavMeshBytes(
  positions: Float32Array,
  indices: Uint32Array,
  config: NavMeshBakeConfig
): Promise<Uint8Array> {
  if (typeof Worker === 'undefined') {
    return bakeSoloNavMeshBytesSync(positions, indices, config);
  }

  try {
    return await bakeSoloNavMeshBytesWorker(positions, indices, config);
  } catch {
    return bakeSoloNavMeshBytesSync(positions, indices, config);
  }
}

function bakeSoloNavMeshBytesSync(
  positions: Float32Array,
  indices: Uint32Array,
  config: NavMeshBakeConfig
): Uint8Array {
  const result = generateSoloNavMesh(positions, indices, config);
  if (!result.success) {
    throw new Error(result.error ?? 'generateSoloNavMesh failed');
  }
  const bytes = exportNavMesh(result.navMesh);
  result.navMesh.destroy();
  return bytes;
}

function bakeSoloNavMeshBytesWorker(
  positions: Float32Array,
  indices: Uint32Array,
  config: NavMeshBakeConfig
): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    let worker: Worker;
    try {
      worker = new Worker(new URL('./bake.worker.ts', import.meta.url), {
        type: 'module',
      });
    } catch (err) {
      reject(err instanceof Error ? err : new Error(String(err)));
      return;
    }

    const cleanup = () => {
      worker.removeEventListener('message', onMessage);
      worker.removeEventListener('error', onError);
      worker.terminate();
    };

    const onMessage = (event: MessageEvent<NavMeshBakeResponse>) => {
      cleanup();
      const data = event.data;
      if (!data || typeof data !== 'object') {
        reject(new Error('NavMesh worker returned empty response'));
        return;
      }
      if (data.ok) {
        resolve(data.bytes);
        return;
      }
      reject(new Error(data.error || 'NavMesh worker bake failed'));
    };

    const onError = (event: ErrorEvent) => {
      cleanup();
      reject(new Error(event.message || 'NavMesh worker error'));
    };

    worker.addEventListener('message', onMessage);
    worker.addEventListener('error', onError);

    // Clone before transfer so a failed worker can still fall back to sync
    // bake with the caller's original (non-detached) arrays.
    const posCopy = positions.slice();
    const idxCopy = indices.slice();
    const request: NavMeshBakeRequest = {
      positions: posCopy,
      indices: idxCopy,
      config,
    };
    worker.postMessage(request, [posCopy.buffer, idxCopy.buffer]);
  });
}
