import type { State } from '../../core';
/* global fetch */
import { loadGltfToSceneWithAnimator } from '../../extras/gltf-bridge';

export interface SceneManifestEntry {
  /** Path to the GLB model file (relative to basePath) */
  model?: string;
  /** Legacy texture paths */
  textures?: string[];
  /** PBR texture set (albedo, normal, roughness, metallic, ao) */
  pbr_textures?: string[];
  /** Animation clip names available in the model */
  animations?: string[];
  /** Path to associated audio file */
  audio?: string;
  /** Bounding box: { min: [x,y,z], max: [x,y,z], size: [x,y,z] } */
  bounds?: { min?: number[]; max?: number[]; size?: number[] };
  /** Pipeline that generated this asset (e.g. 'tripo3d', 'meshy') */
  source_pipeline?: string;
  /* --- Transform overrides (applied after load) --- */
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
}

export interface SceneManifest {
  version: number;
  /** ISO 8601 timestamp of when the manifest was generated (from GameAssets pipeline) */
  generated?: string;
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
