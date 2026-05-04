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

import type { State } from '../core';
import { getRenderingContext, getScene } from '../plugins/rendering';
import { GltfAnimator } from './gltf-animator';

let _ktx2Loader: KTX2Loader | null | undefined = undefined;
let _customTranscoderPath: string | undefined;

/**
 * Override the KTX2 transcoder path. Call before loading any KTX2 textures.
 * The path must be a URL ending with ``/`` pointing to a directory containing
 * ``basis_transcoder.js`` and ``basis_transcoder.wasm``.
 */
export function setKTX2TranscoderPath(path: string): void {
  _customTranscoderPath = path;
  _ktx2Loader = undefined;
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
    console.warn(
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
    const origBitmap = globalThis.createImageBitmap;
    (globalThis as any).createImageBitmap = undefined;

    const restore = () => {
      (globalThis as any).createImageBitmap = origBitmap;
    };

    const wrappedOnLoad = (gltf: GLTF) => {
      restore();
      onLoad(gltf);
    };
    const wrappedOnError = (e: ErrorEvent) => {
      restore();
      onError?.(e);
    };

    try {
      origParse(data, path, wrappedOnLoad, wrappedOnError);
    } catch (e) {
      restore();
      throw e;
    }
  };

  loader.setMeshoptDecoder(MeshoptDecoder);
  if (_ktx2Loader) {
    loader.setKTX2Loader(_ktx2Loader);
  }
  return loader;
}

/**
 * Meshes GLB não carregam `castShadow`/`receiveShadow` por defeito; sem isto o sol direcional não projeta sombras.
 */
export function applyDefaultShadowFlags(root: Object3D): void {
  root.traverse((o) => {
    const m = o as THREE.Mesh & THREE.SkinnedMesh;
    if (m.isMesh === true) {
      m.castShadow = true;
      m.receiveShadow = true;
    }
  });
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
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfLodToScene: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  ensureKTX2FromState(state);
  const loader = createGLTFLoader();
  const root = new THREE.Group();
  root.name = 'gltf-lod-root';

  return Promise.all(
    urls.map((url, i) =>
      loader.loadAsync(url).then((gltf) => {
        applyDefaultShadowFlags(gltf.scene);
        const child = gltf.scene;
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
    scene.add(root);
    return root;
  });
}

export function loadGltfToScene(state: State, url: string): Promise<Group> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfToScene: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  ensureKTX2FromState(state);
  const loader = createGLTFLoader();
  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (gltf) => {
        applyDefaultShadowFlags(gltf.scene);
        scene.add(gltf.scene);
        resolve(gltf.scene);
      },
      undefined,
      reject
    );
  });
}

/**
 * Load glTF/GLB for animation: adds the scene to the render graph and returns the full GLTF (clips + scene).
 * Prefer this when using {@link GltfAnimator}.
 */
export function loadGltfAnimated(state: State, url: string): Promise<GLTF> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfAnimated: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  ensureKTX2FromState(state);
  const loader = createGLTFLoader();
  return loader.loadAsync(url).then((gltf) => {
    applyDefaultShadowFlags(gltf.scene);
    scene.add(gltf.scene);
    return gltf;
  });
}

export interface GltfLoadResult {
  group: Group;
  animator: GltfAnimator | null;
}

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
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfToSceneWithAnimator: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  ensureKTX2FromState(state);
  const loader = createGLTFLoader();
  return new Promise((resolve, reject) => {
    loader.load(
      url,
      (gltf) => {
        applyDefaultShadowFlags(gltf.scene);
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
  });
}
