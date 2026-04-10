/**
 * Bridge for GLB/GLTF assets produced by Text3D, Paint3D, Rigging3D, etc.
 * Adds the loaded scene graph to the VibeGame Three.js scene.
 */
import type { Group, Object3D } from 'three';
import * as THREE from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

import type { State } from '../core';
import { getScene } from '../plugins/rendering';
import { GltfAnimator } from './gltf-animator';

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
export function loadGltfToScene(state: State, url: string): Promise<Group> {
  const scene = getScene(state);
  if (!scene) {
    return Promise.reject(
      new Error(
        'VibeGame loadGltfToScene: no Three.js scene (headless or rendering not ready).'
      )
    );
  }
  const loader = new GLTFLoader();
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
  const loader = new GLTFLoader();
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
  const loader = new GLTFLoader();
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
