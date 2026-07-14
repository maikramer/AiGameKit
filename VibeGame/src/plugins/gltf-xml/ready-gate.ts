import { defineQuery, type State } from '../../core';
import { getCriticalGltfLoadCount } from '../../extras/gltf-bridge';
import { GltfPending } from './components';

const gltfPendingQuery = defineQuery([GltfPending]);

/**
 * Boot assets readiness for the loading gate.
 *
 * Passes when:
 * 1. No **critical** GLTF loads are in flight (lod0 / hero / props). Background
 *    lod1/lod2 streams do not block.
 * 2. Every `GltfPending` entity has finished its kick (loaded=1) — covers the
 *    one-frame race where spawn just created entities but GltfXmlLoadSystem has
 *    not started their fetches yet.
 */
export function gltfAssetsReady(state: State): boolean {
  if (getCriticalGltfLoadCount() > 0) return false;
  for (const eid of gltfPendingQuery(state.world)) {
    if (GltfPending.loaded[eid] !== 1) return false;
  }
  return true;
}
