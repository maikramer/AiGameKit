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
import { getRenderingContext, getScene } from '../plugins/rendering';
import { getSceneGeneration } from './scene-generation';
import { GltfAnimator } from './gltf-animator';

let _ktx2Loader: KTX2Loader | null | undefined = undefined;
let _customTranscoderPath: string | undefined;

// --- GLTF load tracking (for the loading-screen "assets" ready gate) ---
let _activeGltfLoads = 0;
let _anyGltfLoadStarted = false;

/** Number of GLTF/GLB scene loads currently in flight. */
export function getActiveGltfLoadCount(): number {
  return _activeGltfLoads;
}

/** Whether at least one GLTF scene load has ever been started. */
export function hasAnyGltfLoadStarted(): boolean {
  return _anyGltfLoadStarted;
}

/** Wrap a load promise so it counts toward the in-flight asset total. */
function trackGltfLoad<T>(p: Promise<T>): Promise<T> {
  _activeGltfLoads++;
  _anyGltfLoadStarted = true;
  return p.finally(() => {
    _activeGltfLoads = Math.max(0, _activeGltfLoads - 1);
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

function tryInitKTX2(renderer: any): KTX2Loader | null {
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
    p.then((gltf) => disposeObject3DResources(gltf.scene)).catch(() => {});
  }
  gltfMasterCache.clear();
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
    p = loader.loadAsync(url).then((gltf) => {
      applyDefaultShadowFlags(gltf.scene);
      return gltf;
    });
    // Failed loads must not poison the cache (e.g. transient 404 during dev).
    p.catch(() => gltfMasterCache.delete(url));
    gltfMasterCache.set(url, p);
  }
  return p;
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
 * Filhos: nomes `lod0`–`lod2`; só um fica `visible` (por omissão lod1 até ao sistema de LOD).
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

  return trackGltfLoad(
    Promise.all(
      urls.map((url, i) =>
        loadGltfMaster(state, url).then((gltf) => {
          const child = hasSkinnedMesh(gltf.scene)
            ? (cloneSkinnedObject(gltf.scene) as THREE.Group)
            : gltf.scene.clone(true);
          child.name = `lod${i}`;
          child.visible = false;
          child.userData.lodLevel = i;
          return child;
        })
      )
    ).then((children) => {
      for (const c of children) {
        root.add(c);
      }
      if (children[1]) {
        children[1].visible = true;
      } else if (children[0]) {
        children[0].visible = true;
      }
      const orphaned =
        (entityId !== undefined && !state.exists(entityId)) ||
        getSceneGeneration(state) !== gen;
      if (!orphaned) {
        scene.add(root);
      }
      return root;
    })
  );
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
  return trackGltfLoad(
    loadGltfMaster(state, url).then((gltf) => {
      const clone = hasSkinnedMesh(gltf.scene)
        ? (cloneSkinnedObject(gltf.scene) as Group)
        : gltf.scene.clone(true);
      const orphaned =
        (entityId !== undefined && !state.exists(entityId)) ||
        getSceneGeneration(state) !== gen;
      if (!orphaned) {
        scene.add(clone);
      }
      return clone;
    })
  );
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
      }
      return gltf;
    })
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
    })
  );
}
