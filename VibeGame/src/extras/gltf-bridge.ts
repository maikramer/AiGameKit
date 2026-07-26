import { logger } from '../core/utils/logger';
/**
 * Bridge for GLB/GLTF assets produced by Text3D, Paint3D, Rigging3D, etc.
 * Adds the loaded scene graph to the VibeGame Three.js scene.
 */
import type { Group, Object3D } from 'three';
import * as THREE from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { clone as cloneSkinnedObject } from 'three/examples/jsm/utils/SkeletonUtils.js';

import type { State } from '../core';
import {
  getRenderingContext,
  getScene,
  setupCsmMaterials,
} from '../plugins/rendering';
import { getSceneGeneration } from './scene-generation';
import { GltfAnimator } from './gltf-animator';

let _ktx2Loader: KTX2Loader | null | undefined = undefined;
let _customTranscoderPath: string | undefined;

// --- GLTF load tracking (for the loading-screen "assets" ready gate) ---
/**
 * Boot gate counts **unique URLs**, not per-entity clones. Spawning 1800
 * benches that share one GLB must hold the gate once, not 1800 times.
 */
const _criticalUrls = new Set<string>();
const _backgroundUrls = new Set<string>();
/**
 * URLs whose parse already succeeded. A re-request of one of these is a cache
 * hit that resolves on the next microtask — counting it would let a per-frame
 * caller (e.g. a script re-attaching animators to a clip-less master) re-arm
 * the boot gate forever, and the loading screen would never fade.
 */
const _settledMasters = new Set<string>();
/** Anonymous (no-URL) tracks — tests / rare paths. */
let _criticalAnon = 0;
let _backgroundAnon = 0;
let _anyGltfLoadStarted = false;

export type GltfLoadPriority = 'critical' | 'background';

/** In-flight critical GLTF loads — the loading `assets` gate waits on these. */
export function getCriticalGltfLoadCount(): number {
  return _criticalUrls.size + _criticalAnon;
}

/** Total in-flight GLTF loads (critical + background). Debug / HUD. */
export function getActiveGltfLoadCount(): number {
  return (
    _criticalUrls.size + _criticalAnon + _backgroundUrls.size + _backgroundAnon
  );
}

/** URLs currently holding the boot `assets` gate (critical set). */
export function getCriticalGltfInflightUrls(): string[] {
  return [..._criticalUrls];
}

/** Whether at least one GLTF scene load has ever been started. */
export function hasAnyGltfLoadStarted(): boolean {
  return _anyGltfLoadStarted;
}

/** Test helper: reset load counters between cases. */
export function _resetGltfLoadTrackingForTests(): void {
  _criticalUrls.clear();
  _backgroundUrls.clear();
  _settledMasters.clear();
  _criticalAnon = 0;
  _backgroundAnon = 0;
  _anyGltfLoadStarted = false;
}

/**
 * Count a load toward the assets gate. With `url`, duplicate waiters share one
 * credit (gate = unique masters). Without `url`, each promise counts (tests).
 */
function trackGltfLoad<T>(
  p: Promise<T>,
  priority: GltfLoadPriority = 'critical',
  url?: string
): Promise<T> {
  _anyGltfLoadStarted = true;
  const key = url?.trim() || '';
  if (key && _settledMasters.has(key)) return p;
  if (key) {
    const set = priority === 'critical' ? _criticalUrls : _backgroundUrls;
    if (set.has(key)) return p;
    set.add(key);
    return p.then(
      (v) => {
        _settledMasters.add(key);
        set.delete(key);
        return v;
      },
      (e: unknown) => {
        set.delete(key);
        throw e;
      }
    );
  }
  if (priority === 'critical') _criticalAnon++;
  else _backgroundAnon++;
  return p.finally(() => {
    if (priority === 'critical') {
      _criticalAnon = Math.max(0, _criticalAnon - 1);
    } else {
      _backgroundAnon = Math.max(0, _backgroundAnon - 1);
    }
  });
}

/**
 * Override the KTX2 transcoder path. Call before loading any KTX2 textures.
 * The path must be a URL ending with ``/`` pointing to a directory containing
 * ``basis_transcoder.js`` and ``basis_transcoder.wasm``.
 */
export function setKTX2TranscoderPath(path: string): void {
  _customTranscoderPath = path;
  _ktx2Loader = undefined;
}

/**
 * Tear down KTX2 workers, master GLB cache, and load counters.
 * Call on runtime destroy / HMR — Firefox keeps orphan workers+contexts otherwise.
 */
export function disposeGltfBridge(): void {
  clearGltfMasterCache();
  if (_ktx2Loader && typeof _ktx2Loader.dispose === 'function') {
    try {
      _ktx2Loader.dispose();
    } catch (e) {
      logger.warn('[VibeGame] KTX2Loader.dispose failed', e);
    }
  }
  _ktx2Loader = undefined;
  _resetGltfLoadTrackingForTests();
}

function tryInitKTX2(renderer: THREE.WebGLRenderer): KTX2Loader | null {
  if (_ktx2Loader !== undefined) return _ktx2Loader;
  try {
    const transcoderPath =
      _customTranscoderPath ??
      `https://unpkg.com/three@0.${THREE.REVISION}.0/examples/jsm/libs/basis/`;
    _ktx2Loader = new KTX2Loader()
      .setTranscoderPath(transcoderPath)
      .detectSupport(renderer);
    return _ktx2Loader;
  } catch (e) {
    logger.warn(
      '[VibeGame] KTX2Loader init failed — KTX2 textures disabled. ' +
        'Call setKTX2TranscoderPath() with a valid URL, or ensure ' +
        'basis_transcoder.js / .wasm are accessible from node_modules.',
      e
    );
    _ktx2Loader = null;
    return null;
  }
}

function ensureKTX2FromState(state: State): void {
  if (_ktx2Loader !== undefined) return;
  const ctx = getRenderingContext(state);
  if (ctx.renderer) tryInitKTX2(ctx.renderer);
}

/**
 * Ensure KTX2 is initialized from the active WebGL renderer when possible.
 * Returns false until a renderer exists (callers should keep URLs queued).
 * Returns true after an init attempt — even if KTX2 is unavailable (null), so
 * meshopt/plain GLBs can still prefetch; KTX2-required assets fail clearly.
 */
export function ensureKTX2LoaderReady(state: State): boolean {
  if (_ktx2Loader !== undefined) return true;
  const ctx = getRenderingContext(state);
  if (!ctx.renderer) return false;
  tryInitKTX2(ctx.renderer);
  return true;
}

/**
 * Create a {@link GLTFLoader} with KTX2 texture support attached (when available).
 *
 * Forces TextureLoader (img-based) for embedded textures instead of ImageBitmapLoader
 * (fetch-based). GLTFLoader r168 selects ImageBitmapLoader when `createImageBitmap` is
 * available, but its `fetch(blobUrl) → createImageBitmap(blob)` pipeline can fail on
 * blob: URLs in some environments. TextureLoader's `<img>` approach is universally
 * compatible.
 *
 * Concurrency-safe: nested/concurrent parse() calls share a single disable-depth
 * counter so the global is only restored after the last parse completes.
 *
 * @param manager - Optional Three.js LoadingManager.
 * @returns A configured GLTFLoader instance.
 */
export function createGLTFLoader(manager?: THREE.LoadingManager): GLTFLoader {
  const loader = new GLTFLoader(manager);

  // Intercept parse() to temporarily disable createImageBitmap so the internal
  // GLTFParser constructor picks TextureLoader instead of ImageBitmapLoader.
  const origParse = loader.parse.bind(loader);
  loader.parse = function (
    data: ArrayBuffer | string,
    path: string,
    onLoad: (gltf: GLTF) => void,
    onError?: (event: ErrorEvent) => void
  ): void {
    acquireImageBitmapDisable();

    const wrappedOnLoad = (gltf: GLTF) => {
      releaseImageBitmapDisable();
      onLoad(gltf);
    };
    const wrappedOnError = (e: ErrorEvent) => {
      releaseImageBitmapDisable();
      onError?.(e);
    };

    try {
      origParse(data, path, wrappedOnLoad, wrappedOnError);
    } catch (e) {
      releaseImageBitmapDisable();
      throw e;
    }
  };

  loader.setMeshoptDecoder(MeshoptDecoder);
  if (_ktx2Loader) {
    loader.setKTX2Loader(_ktx2Loader);
  }
  return loader;
}

// --- createImageBitmap disable ref-count --------------------------------
// Concurrent GLTF parses share one disable-depth so the global is only restored
// after the LAST parse completes (early restores used to permanently disable the
// API for in-flight parses from sibling loaders).
let _imageBitmapDisableDepth = 0;
let _origCreateImageBitmap: typeof globalThis.createImageBitmap | undefined;

function acquireImageBitmapDisable(): void {
  if (_imageBitmapDisableDepth === 0) {
    _origCreateImageBitmap = globalThis.createImageBitmap;
    (globalThis as Record<string, unknown>).createImageBitmap = undefined;
  }
  _imageBitmapDisableDepth++;
}

function releaseImageBitmapDisable(): void {
  if (_imageBitmapDisableDepth === 0) return;
  _imageBitmapDisableDepth--;
  if (_imageBitmapDisableDepth === 0) {
    (globalThis as Record<string, unknown>).createImageBitmap =
      _origCreateImageBitmap;
    _origCreateImageBitmap = undefined;
  }
}

/** True if the loaded scene contains skinned meshes (needs SkeletonUtils.clone). */
function hasSkinnedMesh(root: Object3D): boolean {
  let found = false;
  root.traverse((o) => {
    if ((o as THREE.SkinnedMesh).isSkinnedMesh) found = true;
  });
  return found;
}

/**
 * glTF defaults `metallicFactor` to 1.0 when omitted. Asset-pipeline GLBs that
 * only carry an albedo texture (no metallic-roughness map) then render almost
 * black under punctual lights. Treat those materials as dielectric.
 */
export function normalizeGltfMaterials(root: Object3D): void {
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const materials = Array.isArray(mesh.material)
      ? mesh.material
      : [mesh.material];
    for (const mat of materials) {
      const std = mat as THREE.MeshStandardMaterial;
      if (
        std?.isMeshStandardMaterial &&
        std.metalness === 1 &&
        !std.metalnessMap
      ) {
        std.metalness = 0;
        std.needsUpdate = true;
      }
    }
  });
}

/**
 * Meshes GLB não carregam `castShadow`/`receiveShadow` por defeito; sem isto o sol direcional não projeta sombras.
 */
export function applyDefaultShadowFlags(root: Object3D): void {
  normalizeGltfMaterials(root);
  root.traverse((o) => {
    const m = o as THREE.Mesh & THREE.SkinnedMesh;
    if (m.isMesh === true) {
      m.castShadow = true;
      m.receiveShadow = true;
      // SkinnedMesh frustum tests use the bind-pose sphere, not the posed
      // skeleton. Animated characters then blink out whenever the bind origin
      // leaves the view frustum (common on CCT enemies that turn in place).
      if (m.isSkinnedMesh === true) {
        m.frustumCulled = false;
      }
    }
  });
}

// --- Master GLB cache ---------------------------------------------------
// Loading the same URL N times used to download + parse + upload N copies.
// Parse once per URL; consumers receive `scene.clone(true)`, which clones the
// node hierarchy but SHARES geometries and materials — one GPU upload per
// asset no matter how many props use it. Skinned/animated paths stay
// uncached (clones would share skeletons).
const gltfMasterCache = new Map<string, Promise<GLTF>>();

/**
 * Drop one master GLB from the cache (use after a level/scene transition to
 * free the GPU resources it was pinning). The caller is responsible for
 * ensuring no live clone of this master remains in the scene.
 */
export function evictGltfMaster(url: string): boolean {
  _settledMasters.delete(url);
  return gltfMasterCache.delete(url);
}

/** Drop every cached master GLB. See {@link evictGltfMaster}. */
export function clearGltfMasterCache(): number {
  const n = gltfMasterCache.size;
  // Dispose each master's GPU resources. Clones share geometry/material with
  // the master, so this releases the single shared upload — safe at teardown
  // where group-registry onDestroy + auto-instance dispose have already torn
  // down every live clone. `.then` covers in-flight parses that resolve after
  // clear; rejecting loads have nothing to dispose.
  for (const p of gltfMasterCache.values()) {
    p.then((gltf) => disposeObject3DResources(gltf.scene)).catch((e) =>
      logger.warn('clearGltfMasterCache disposal failed', e)
    );
  }
  gltfMasterCache.clear();
  _settledMasters.clear();
  return n;
}

/** Internal: groups tagged with this flag own private GPU resources that the
 * engine must dispose when their owning entity is destroyed. Set only on
 * animated/non-cached GLB loads. */
export const OWNED_GPU_FLAG = 'vibegameOwnedGpu';

function markGroupOwnedGpu(group: THREE.Object3D): void {
  (group.userData as Record<string, unknown>)[OWNED_GPU_FLAG] = true;
}

/**
 * Returns true when {@link markGroupOwnedGpu} flagged `root` — i.e. its
 * geometries/materials/textures are NOT shared with the master cache and the
 * engine must dispose them on entity destroy.
 */
export function isGroupOwnedGpu(root: THREE.Object3D): boolean {
  return (root.userData as Record<string, unknown>)[OWNED_GPU_FLAG] === true;
}

/** Dispose every geometry/material/texture reachable from `root`. */
export function disposeObject3DResources(root: THREE.Object3D): void {
  const disposedGeometries = new Set<THREE.BufferGeometry>();
  const disposedMaterials = new Set<THREE.Material>();
  const disposedTextures = new Set<THREE.Texture>();
  root.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh !== true) return;
    const geos = Array.isArray(mesh.geometry) ? mesh.geometry : [mesh.geometry];
    for (const g of geos) {
      if (g && !disposedGeometries.has(g)) {
        g.dispose();
        disposedGeometries.add(g);
      }
    }
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of mats) {
      if (!m || disposedMaterials.has(m)) continue;
      disposedMaterials.add(m);
      for (const k in m) {
        const v = (m as unknown as Record<string, unknown>)[k];
        if (v && typeof v === 'object' && 'isTexture' in v) {
          const tex = v as THREE.Texture;
          if (!disposedTextures.has(tex)) {
            tex.dispose();
            disposedTextures.add(tex);
          }
        }
      }
      m.dispose();
    }
  });
}

/** Default ceiling for a single GLB fetch+parse (KTX2 hang, dead CDN, …). */
const GLTF_MASTER_LOAD_TIMEOUT_MS = 45_000;

function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const t = setTimeout(() => {
      reject(
        new Error(
          `GLTF load timed out after ${ms}ms: ${label} ` +
            `(KTX2/basis stuck, rede lenta, ou ficheiro em falta)`
        )
      );
    }, ms);
    p.then(
      (v) => {
        clearTimeout(t);
        resolve(v);
      },
      (e: unknown) => {
        clearTimeout(t);
        reject(e);
      }
    );
  });
}

/**
 * Parse a GLB once and cache it. The returned GLTF is the shared master —
 * callers must NOT mutate or add `gltf.scene` to a scene; clone it instead.
 * Mutating a shared material affects every clone.
 */
export function loadGltfMaster(state: State, url: string): Promise<GLTF> {
  ensureKTX2FromState(state);
  let p = gltfMasterCache.get(url);
  if (!p) {
    const loader = createGLTFLoader();
    p = withTimeout(
      loader.loadAsync(url).then((gltf) => {
        applyDefaultShadowFlags(gltf.scene);
        _settledMasters.add(url);
        return gltf;
      }),
      GLTF_MASTER_LOAD_TIMEOUT_MS,
      url
    );
    // Failed loads must not poison the cache (e.g. transient 404 during dev).
    p.catch(() => gltfMasterCache.delete(url));
    gltfMasterCache.set(url, p);
  }
  return p;
}

/**
 * Like {@link loadGltfMaster}, but counts toward the boot `assets` gate
 * (`critical`) or streams without blocking (`background`). Use for paths that
 * bypass the scene-load wrappers (e.g. auto-instancing).
 */
export function loadGltfMasterTracked(
  state: State,
  url: string,
  priority: GltfLoadPriority = 'critical'
): Promise<GLTF> {
  return trackGltfLoad(loadGltfMaster(state, url), priority, url);
}

/** Test helper: wrap an arbitrary promise with the GLTF load tracker. */
export function _trackGltfLoadForTests<T>(
  p: Promise<T>,
  priority: GltfLoadPriority = 'critical',
  url?: string
): Promise<T> {
  return trackGltfLoad(p, priority, url);
}

/**
 * Load a glTF/GLB from URL and attach it to the current rendering scene.
 *
 * @param state - VibeGame ECS state (after runtime started with DOM rendering).
 * @param url - Absolute or site-root URL (e.g. ``/assets/models/hero.glb``).
 * @returns The loaded root object (typically scaled/positioned by the asset).
 */
/**
 * Carrega três GLBs (LOD0/1/2), agrupa-os num único `Group` e adiciona-o à cena.
 * Filhos: nomes `lod0`–`lod2`; só um fica `visible` (por omissão lod0 até ao sistema de LOD).
 */
export function loadGltfLodToScene(
  state: State,
  urls: readonly [string, string, string]
): Promise<Group> {
  return loadGltfLodToSceneForEntity(state, urls, undefined);
}

/**
 * Entity-aware variant: when ``entityId`` is provided the load bails (without
 * parenting to the scene) if the entity no longer exists or the scene
 * generation changed since the load started. Clones share geometry/material
 * with the master cache, so an orphaned group is simply dropped — the shared
 * GPU resources are released when the master cache is cleared.
 *
 * Boot waits only on **lod0** (critical). lod1/lod2 stream as background loads
 * and are attached when ready — {@link GltfLodSystem} picks them up once
 * `children.length >= 2`.
 */
export function loadGltfLodToSceneForEntity(
  state: State,
  urls: readonly [string, string, string],
  entityId: number | undefined
): Promise<Group> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfLodToScene: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  const gen = getSceneGeneration(state);
  const root = new THREE.Group();
  root.name = 'gltf-lod-root';

  const isOrphaned = (): boolean =>
    (entityId !== undefined && !state.exists(entityId)) ||
    getSceneGeneration(state) !== gen;

  const cloneLodChild = (gltf: GLTF, level: number): THREE.Group => {
    const child = hasSkinnedMesh(gltf.scene)
      ? (cloneSkinnedObject(gltf.scene) as THREE.Group)
      : gltf.scene.clone(true);
    child.name = `lod${level}`;
    child.visible = level === 0;
    child.userData.lodLevel = level;
    return child;
  };

  const sortLodChildren = (): void => {
    root.children.sort(
      (a, b) =>
        ((a.userData.lodLevel as number) ?? 0) -
        ((b.userData.lodLevel as number) ?? 0)
    );
  };

  // Gate waits on lod0 **master** only — per-entity clone work must not stack
  // thousands of critical credits for the same URL.
  return loadGltfMasterTracked(state, urls[0], 'critical').then((gltf) => {
    if (isOrphaned()) return root;
    root.add(cloneLodChild(gltf, 0));
    scene.add(root);
    setupCsmMaterials(state, root);

    // Stream higher LODs without holding the boot gate.
    for (const level of [1, 2] as const) {
      const url = urls[level];
      if (!url) continue;
      void loadGltfMasterTracked(state, url, 'background')
        .then((gltfLod) => {
          if (isOrphaned()) return;
          // Skip if this level already attached (retry / cache race).
          if (
            root.children.some((c) => (c.userData.lodLevel as number) === level)
          ) {
            return;
          }
          root.add(cloneLodChild(gltfLod, level));
          sortLodChildren();
          setupCsmMaterials(state, root);
        })
        .catch((err: unknown) => {
          const msg = err instanceof Error ? err.message : String(err);
          logger.warn(`[gltf-lod] lod${level} "${url}" failed: ${msg}`);
        });
    }

    return root;
  });
}

export function loadGltfToScene(state: State, url: string): Promise<Group> {
  return loadGltfToSceneForEntity(state, url, undefined);
}

// Entity-aware variant: bails (no scene.add) when entityId is gone or the scene
// generation changed. Clone shares GPU resources with the cached master, so an
// orphan is just dropped — disposal happens via clearGltfMasterCache.
export function loadGltfToSceneForEntity(
  state: State,
  url: string,
  entityId: number | undefined
): Promise<Group> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfToScene: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  const gen = getSceneGeneration(state);
  return loadGltfMasterTracked(state, url, 'critical').then((gltf) => {
    const clone = hasSkinnedMesh(gltf.scene)
      ? (cloneSkinnedObject(gltf.scene) as Group)
      : gltf.scene.clone(true);
    const orphaned =
      (entityId !== undefined && !state.exists(entityId)) ||
      getSceneGeneration(state) !== gen;
    if (!orphaned) {
      scene.add(clone);
      setupCsmMaterials(state, clone);
    }
    return clone;
  });
}

/**
 * Load glTF/GLB for animation: adds the scene to the render graph and returns the full GLTF (clips + scene).
 * Prefer this when using {@link GltfAnimator}.
 */
export function loadGltfAnimated(state: State, url: string): Promise<GLTF> {
  return loadGltfAnimatedForEntity(state, url, undefined);
}

// Entity-aware variant: bails (no scene.add) when entityId is gone or the scene
// generation changed. This path loads fresh (not from the master cache) and
// owns its GPU resources, so an orphan's resources are disposed to avoid leaks.
export function loadGltfAnimatedForEntity(
  state: State,
  url: string,
  entityId: number | undefined
): Promise<GLTF> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfAnimated: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  const gen = getSceneGeneration(state);
  ensureKTX2FromState(state);
  const loader = createGLTFLoader();
  return trackGltfLoad(
    withTimeout(
      loader.loadAsync(url).then((gltf) => {
        applyDefaultShadowFlags(gltf.scene);
        markGroupOwnedGpu(gltf.scene);
        const orphaned =
          (entityId !== undefined && !state.exists(entityId)) ||
          getSceneGeneration(state) !== gen;
        if (orphaned) {
          disposeObject3DResources(gltf.scene);
        } else {
          scene.add(gltf.scene);
          setupCsmMaterials(state, gltf.scene);
        }
        return gltf;
      }),
      GLTF_MASTER_LOAD_TIMEOUT_MS,
      url
    ),
    'critical',
    url
  );
}

export interface GltfLoadResult {
  group: Group;
  animator: GltfAnimator | null;
}

export { validateGltf } from './gltf-validator';
export type {
  GltfIssueSeverity,
  GltfValidationIssue,
  GltfValidationReport,
  ValidateGltfOptions,
} from './gltf-validator';

/**
 * Load a glTF/GLB from URL, attach it to the current rendering scene, and optionally
 * wrap embedded clips in a {@link GltfAnimator}.
 *
 * @param state - VibeGame ECS state (after runtime started with DOM rendering).
 * @param url - Absolute or site-root URL (e.g. ``/assets/models/hero.glb``).
 * @param options - Optional {@link GltfAnimator} settings (e.g. crossfade duration).
 */
export function loadGltfToSceneWithAnimator(
  state: State,
  url: string,
  options?: { crossfadeDuration?: number }
): Promise<GltfLoadResult> {
  return loadGltfToSceneWithAnimatorForEntity(state, url, options, undefined);
}

// Entity-aware variant: bails (no scene.add, animator=null) when entityId is
// gone or the scene generation changed; orphan GPU resources are disposed.
export function loadGltfToSceneWithAnimatorForEntity(
  state: State,
  url: string,
  options: { crossfadeDuration?: number } | undefined,
  entityId: number | undefined
): Promise<GltfLoadResult> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfToSceneWithAnimator: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  const gen = getSceneGeneration(state);
  ensureKTX2FromState(state);
  const loader = createGLTFLoader();
  return trackGltfLoad(
    withTimeout(
      new Promise<GltfLoadResult>((resolve, reject) => {
        loader.load(
          url,
          (gltf) => {
            applyDefaultShadowFlags(gltf.scene);
            markGroupOwnedGpu(gltf.scene);
            const orphaned =
              (entityId !== undefined && !state.exists(entityId)) ||
              getSceneGeneration(state) !== gen;
            if (orphaned) {
              disposeObject3DResources(gltf.scene);
              resolve({ group: gltf.scene, animator: null });
              return;
            }
            scene.add(gltf.scene);
            setupCsmMaterials(state, gltf.scene);
            const animator =
              gltf.animations.length > 0
                ? new GltfAnimator(gltf, {
                    crossfadeDuration: options?.crossfadeDuration,
                  })
                : null;
            resolve({
              group: gltf.scene,
              animator,
            });
          },
          undefined,
          reject
        );
      }),
      GLTF_MASTER_LOAD_TIMEOUT_MS,
      url
    ),
    'critical',
    url
  );
}
