import { logger } from '../../core/utils/logger';
import type { State } from '../../core';
import * as THREE from 'three';

import {
  ensureKTX2LoaderReady,
  loadGltfMaster,
} from '../../extras/gltf-bridge';

/** Cap parallel AABB prefetches so boot scene loads / KTX2 workers are not starved. */
const PREFETCH_MAX_INFLIGHT = 3;

/** Chave = URL normalizada (trim); valores em espaço local do root do GLB (Y up). */
const boundsByUrl = new Map<
  string,
  {
    minX: number;
    minY: number;
    minZ: number;
    maxX: number;
    maxY: number;
    maxZ: number;
  }
>();

const warnedMissing = new Set<string>();
const prefetchInflight = new Set<string>();
/** Queued until KTX2/renderer is ready (parse-time prefetch often runs too early). */
const prefetchQueued = new Set<string>();
const prefetchFailed = new Set<string>();

/** Clear URL-scoped bounds/warning state on scene/plugin teardown. */
export function clearGltfBoundsCache(): void {
  boundsByUrl.clear();
  warnedMissing.clear();
  prefetchInflight.clear();
  prefetchQueued.clear();
  prefetchFailed.clear();
}

export function normalizeGltfUrlKey(url: string): string {
  return url.trim();
}

/**
 * Regista o intervalo Y do AABB do modelo (ex.: após `loadGltfToScene`, antes de aplicar transform da entidade).
 * Usado pelo spawn com `ground-align="aabb"` para levantar a origem até o solo (`-minY * escala` ao longo da normal).
 */
export function registerGltfLocalYBounds(
  url: string,
  root: THREE.Object3D
): void {
  const key = normalizeGltfUrlKey(url);
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  const existing = boundsByUrl.get(key);
  // Keep a feet-at-origin seed (minY ≈ 0) when setFromObject later reports a
  // centered box (minY deeply negative). Catch-up would add −minY on top of
  // instanced prim.local and the trunk would float.
  if (existing && existing.minY >= -0.15 && box.min.y < existing.minY - 0.5) {
    return;
  }
  boundsByUrl.set(key, {
    minX: box.min.x,
    minY: box.min.y,
    minZ: box.min.z,
    maxX: box.max.x,
    maxY: box.max.y,
    maxZ: box.max.z,
  });
  prefetchQueued.delete(key);
  prefetchInflight.delete(key);
  prefetchFailed.delete(key);
}

export function getGltfLocalYBounds(
  url: string
): { minY: number; maxY: number } | null {
  const full = boundsByUrl.get(normalizeGltfUrlKey(url));
  return full ? { minY: full.minY, maxY: full.maxY } : null;
}

/**
 * Semeia o AABB local a partir do pré-cálculo do GameAssets
 * (`gameassets_handoff.json`) — evita `Box3.setFromObject` e permite ao
 * spawner com `ground-align="aabb"` levantar a origem antes do GLB carregar.
 * `aabb` está em espaço do root do GLB (contrato do `glb_extract_meta`).
 */
export function seedGltfPrecomputedBounds(
  url: string,
  aabb: { min: [number, number, number]; max: [number, number, number] }
): void {
  const key = normalizeGltfUrlKey(url);
  if (!key || boundsByUrl.has(key)) return;
  const [minX, minY, minZ] = aabb.min;
  const [maxX, maxY, maxZ] = aabb.max;
  boundsByUrl.set(key, { minX, minY, minZ, maxX, maxY, maxZ });
  prefetchQueued.delete(key);
  prefetchInflight.delete(key);
  prefetchFailed.delete(key);
}

export function getGltfLocalAABB(url: string): {
  minX: number;
  minY: number;
  minZ: number;
  maxX: number;
  maxY: number;
  maxZ: number;
} | null {
  return boundsByUrl.get(normalizeGltfUrlKey(url)) ?? null;
}

export function isGltfBoundsPrefetchInflight(url: string): boolean {
  const key = normalizeGltfUrlKey(url);
  return prefetchInflight.has(key) || prefetchQueued.has(key);
}

function startPrefetchFetch(state: State, key: string): void {
  if (boundsByUrl.has(key) || prefetchInflight.has(key)) return;
  prefetchQueued.delete(key);
  prefetchInflight.add(key);
  // Share the master GLB cache with scene/instance loads (no second KTX2 parse).
  // Not trackGltfLoad — AABB must not hold the boot `assets` gate.
  void loadGltfMaster(state, key)
    .then((gltf) => {
      registerGltfLocalYBounds(key, gltf.scene);
    })
    .catch((err: unknown) => {
      prefetchInflight.delete(key);
      prefetchFailed.add(key);
      const msg = err instanceof Error ? err.message : String(err);
      if (!warnedMissing.has(key)) {
        warnedMissing.add(key);
        logger.warn(
          `[spawn-group] AABB prefetch falhou para "${key}": ${msg}. ` +
            `Catch-up aplica lift quando o GLB carregar na cena.`
        );
      }
    })
    .finally(() => {
      prefetchInflight.delete(key);
      pumpPrefetchQueue(state);
    });
}

function pumpPrefetchQueue(state: State): void {
  while (
    prefetchInflight.size < PREFETCH_MAX_INFLIGHT &&
    prefetchQueued.size > 0
  ) {
    const key = prefetchQueued.values().next().value as string | undefined;
    if (!key) break;
    startPrefetchFetch(state, key);
  }
}

/**
 * Queue a background AABB prefetch. Safe at XML parse time (before renderer /
 * KTX2 exist). Call {@link flushGltfBoundsPrefetch} once rendering can init KTX2.
 */
export function prefetchGltfLocalYBounds(url: string): void {
  const key = normalizeGltfUrlKey(url);
  if (!key || boundsByUrl.has(key)) return;
  if (prefetchInflight.has(key) || prefetchQueued.has(key)) return;
  prefetchQueued.add(key);
}

/**
 * Start queued prefetches once KTX2/meshopt loaders are available.
 * Caps concurrency; uses shared {@link loadGltfMaster} cache.
 */
export function flushGltfBoundsPrefetch(state: State): void {
  if (prefetchQueued.size === 0 && prefetchInflight.size === 0) return;
  if (!ensureKTX2LoaderReady(state)) return;
  pumpPrefetchQueue(state);
}
