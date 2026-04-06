import type { State } from '../../core';
/* global fetch */
import { loadGltfToSceneWithAnimator } from '../../extras/gltf-bridge';

export interface SceneManifestEntry {
  model?: string;
  textures?: string[];
  animations?: string[];
  audio?: string;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
}

export interface SceneManifest {
  version: number;
  assets: Record<string, SceneManifestEntry>;
}

/**
 * Load a gameassets_manifest.json and spawn all model entities into the scene.
 * Returns the loaded manifest for inspection.
 */
export async function loadSceneManifest(
  state: State,
  url: string,
  basePath = '/'
): Promise<SceneManifest> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `Failed to load manifest: ${response.status} ${response.statusText}`
    );
  }

  const manifest: SceneManifest = await response.json();

  for (const [name, entry] of Object.entries(manifest.assets)) {
    if (!entry.model) continue;

    try {
      const modelUrl = basePath + entry.model;
      const result = await loadGltfToSceneWithAnimator(state, modelUrl);

      if (entry.position) {
        result.group.position.set(...entry.position);
      }
      if (entry.rotation) {
        result.group.rotation.set(...entry.rotation);
      }
      if (entry.scale) {
        result.group.scale.set(...entry.scale);
      }
    } catch (err) {
      console.warn(`[SceneManifest] Failed to load asset '${name}':`, err);
    }
  }

  return manifest;
}
