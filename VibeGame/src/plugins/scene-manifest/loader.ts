import type { State } from '../../core';
/* global fetch */
import { loadGltfToSceneWithAnimator } from '../../extras/gltf-bridge';
import { AudioEmitter } from '../audio/components';
import { registerAudioClip } from '../audio/systems';

export interface SceneManifestEntry {
  model?: string;
  textures?: string[];
  pbr_textures?: string[];
  animations?: string[];
  audio?: string;
  bounds?: { min?: number[]; max?: number[]; size?: number[] };
  source_pipeline?: string;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: [number, number, number];
}

export interface SceneManifest {
  version: number;
  generated?: string;
  assets: Record<string, SceneManifestEntry>;
}

export interface HandoffRow {
  id: string;
  public_id?: string;
  model?: { kind?: string; url?: string; dest?: string; source?: string };
  audio?: { url?: string; dest?: string; source?: string };
}

export interface HandoffManifest {
  version: number;
  generated_at?: string;
  public_dir?: string;
  assets_base_url?: string;
  rows?: HandoffRow[];
}

type RawManifest = SceneManifest & Partial<HandoffManifest>;

function isHandoffFormat(data: RawManifest): data is RawManifest & { rows: HandoffRow[] } {
  return Array.isArray((data as Partial<HandoffManifest>).rows);
}

async function loadOldFormat(state: State, manifest: SceneManifest, basePath: string): Promise<void> {
  for (const [name, entry] of Object.entries(manifest.assets)) {
    if (!entry.model) continue;

    try {
      const modelUrl = basePath + entry.model;
      const result = await loadGltfToSceneWithAnimator(state, modelUrl);

      if (entry.position) result.group.position.set(...entry.position);
      if (entry.rotation) result.group.rotation.set(...entry.rotation);
      if (entry.scale) result.group.scale.set(...entry.scale);
    } catch (err) {
      console.warn(`[SceneManifest] Failed to load asset '${name}':`, err);
    }
  }
}

async function loadHandoffFormat(state: State, manifest: RawManifest & { rows: HandoffRow[] }, basePath: string): Promise<void> {
  for (const row of manifest.rows) {
    if (row.model?.url) {
      try {
        await loadGltfToSceneWithAnimator(state, row.model.url);
      } catch (err) {
        console.warn(`[SceneManifest] Failed to load model '${row.id}':`, err);
      }
    } else if (row.audio?.url) {
      const eid = state.createEntity();
      state.addComponent(eid, AudioEmitter, {
        volume: 0.7,
        loop: 1,
        spatial: 0,
        playing: 1,
      });
      AudioEmitter.clipPath[eid] = eid;
      registerAudioClip(eid, basePath + row.audio.url);
    }
  }
}

export async function loadSceneManifest(
  state: State,
  url = '/assets/gameassets_handoff.json',
  basePath = '/'
): Promise<SceneManifest> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`Failed to load manifest: ${response.status} ${response.statusText}`);
  }

  const data: RawManifest = await response.json();

  if (isHandoffFormat(data)) {
    await loadHandoffFormat(state, data, basePath);
  } else {
    await loadOldFormat(state, data as SceneManifest, basePath);
  }

  return data as SceneManifest;
}
