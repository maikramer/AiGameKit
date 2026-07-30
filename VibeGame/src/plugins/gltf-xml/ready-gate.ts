import { defineQuery, type State } from '../../core';
import {
  getActiveGltfLoadCount,
  getCriticalGltfInflightUrls,
  getCriticalGltfLoadCount,
  getCriticalGltfLoadProgress,
} from '../../extras/gltf-bridge';
import { GltfPending } from './components';
import { getGltfLodUrls, getGltfUrl, isGltfInFlight } from './context';

const gltfPendingQuery = defineQuery([GltfPending]);

/**
 * Boot assets readiness for the loading gate.
 *
 * Passes when:
 * 1. No **critical** unique-URL masters are in flight (lod0 / hero / props).
 *    Background lod1/lod2 do not block. Per-entity clones of an already-cached
 *    master do not stack credits.
 * 2. Every `GltfPending` has been **kicked** (in-flight or loaded=1) — covers
 *    the race where spawn just created entities but GltfXmlLoadSystem has not
 *    started their fetches yet. Does **not** wait for thousands of clone
 *    attach completions after the shared master is ready.
 */
export function gltfAssetsReady(state: State): boolean {
  if (getCriticalGltfLoadCount() > 0) return false;
  for (const eid of gltfPendingQuery(state.world)) {
    if (GltfPending.loaded[eid] === 1) continue;
    if (isGltfInFlight(state, eid)) continue;
    return false;
  }
  return true;
}

/** Snapshot for loading UI / stall logs when the `assets` gate is held. */
export function describeGltfAssetsPending(state: State): {
  critical: number;
  active: number;
  pendingEntities: number;
  /** Unique critical masters settled this boot. */
  done: number;
  /** Unique critical masters requested this boot (grows as loads start). */
  total: number;
  /** Alias of `critical` — in-flight critical remaining. */
  remaining: number;
  sampleUrls: string[];
  criticalUrls: string[];
} {
  const sampleUrls: string[] = [];
  let pendingEntities = 0;
  for (const eid of gltfPendingQuery(state.world)) {
    if (GltfPending.loaded[eid] === 1) continue;
    if (isGltfInFlight(state, eid)) continue;
    pendingEntities++;
    if (sampleUrls.length >= 6) continue;
    const lod = getGltfLodUrls(state, eid);
    const url = lod?.[0] ?? getGltfUrl(state, eid);
    if (url) sampleUrls.push(url);
    else sampleUrls.push(`eid:${eid}`);
  }
  const criticalUrls = getCriticalGltfInflightUrls();
  const progress = getCriticalGltfLoadProgress();
  return {
    critical: progress.remaining,
    active: getActiveGltfLoadCount(),
    pendingEntities,
    done: progress.done,
    total: progress.total,
    remaining: progress.remaining,
    sampleUrls: sampleUrls.length > 0 ? sampleUrls : criticalUrls.slice(0, 6),
    criticalUrls,
  };
}
