/**
 * Bridge for GLB/GLTF assets produced by Text3D, Paint3D, Rigging3D, etc.
 * Parses with @loaders.gl/gltf (post-process + encode), then builds Three.js
 * scene and clips via GLTFLoader.parseAsync.
 */
import { encodeSync, load } from '@loaders.gl/core';
import { DracoLoader } from '@loaders.gl/draco';
import {
  GLTFLoader as LoadersGLTFLoader,
  GLTFWriter,
  postProcessGLTF,
} from '@loaders.gl/gltf';
import type { Group } from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { GLTFLoader as ThreeGLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';

import type { State } from '../core';
import { getScene } from '../plugins/rendering';
import { GltfAnimator } from './gltf-animator';

const threeGltfLoader = new ThreeGLTFLoader();

/**
 * Load glTF/GLB via loaders.gl, then round-trip to Three.js GLTF (scene + animations).
 */
async function loadGltfAsThree(url: string): Promise<GLTF> {
  const gltfWithBuffers = await load(url, LoadersGLTFLoader, {
    DracoLoader,
    gltf: {
      decompressMeshes: true,
      loadBuffers: true,
      loadImages: true,
    },
  });
  const processed = postProcessGLTF(gltfWithBuffers);
  const arrayBuffer = encodeSync(processed, GLTFWriter);
  return threeGltfLoader.parseAsync(arrayBuffer, '');
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
  return loadGltfAsThree(url).then((gltf) => {
    scene.add(gltf.scene);
    return gltf.scene;
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
  return loadGltfAsThree(url).then((gltf) => {
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
  return loadGltfAsThree(url).then((gltf) => {
    scene.add(gltf.scene);
    const animator =
      gltf.animations.length > 0
        ? new GltfAnimator(gltf, {
            crossfadeDuration: options?.crossfadeDuration,
          })
        : null;
    return {
      group: gltf.scene,
      animator,
    };
  });
}
