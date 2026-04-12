import { type State, type System } from '../../core';
import { loadGltfToSceneWithAnimator } from '../../extras/gltf-bridge';
import type { GltfAnimator } from '../../extras/gltf-animator';
import { AudioSource } from '../audio/components';
import { registerAudioClip } from '../audio/systems';
import { TextureRecipe } from '../rendering/texture-recipe';
import { setTextureRecipeUrl } from '../rendering/texture-recipe-system';

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
  pbr_textures?: string[];
}

export interface HandoffManifest {
  version: number;
  generated_at?: string;
  public_dir?: string;
  assets_base_url?: string;
  rows?: HandoffRow[];
}

export const animatorMap = new Map<number, GltfAnimator>();

const stateTrackedEntities = new WeakMap<State, Set<number>>();

function storeAnimator(state: State, animator: GltfAnimator | null): number {
  const eid = state.createEntity();

  if (!animator) return eid;

  animatorMap.set(eid, animator);

  let tracked = stateTrackedEntities.get(state);
  if (!tracked) {
    tracked = new Set();
    stateTrackedEntities.set(state, tracked);
  }
  tracked.add(eid);

  const clipNames = animator.clipNames;
  if (clipNames.length > 0) {
    animator.play(clipNames[0]);
  }

  return eid;
}

type RawManifest = SceneManifest & Partial<HandoffManifest>;

function isHandoffFormat(
  data: RawManifest
): data is RawManifest & { rows: HandoffRow[] } {
  return Array.isArray((data as Partial<HandoffManifest>).rows);
}

async function loadOldFormat(
  state: State,
  manifest: SceneManifest,
  basePath: string
): Promise<void> {
  for (const [name, entry] of Object.entries(manifest.assets)) {
    if (!entry.model) continue;

    try {
      const modelUrl = basePath + entry.model;
      const result = await loadGltfToSceneWithAnimator(state, modelUrl);

      if (entry.position) result.group.position.set(...entry.position);
      if (entry.rotation) result.group.rotation.set(...entry.rotation);
      if (entry.scale) result.group.scale.set(...entry.scale);

      storeAnimator(state, result.animator);
    } catch (err) {
      console.warn(`[SceneManifest] Failed to load asset '${name}':`, err);
    }
  }
}

async function loadHandoffFormat(
  state: State,
  manifest: RawManifest & { rows: HandoffRow[] },
  basePath: string
): Promise<void> {
  for (const row of manifest.rows) {
    if (row.model?.url) {
      try {
        const result = await loadGltfToSceneWithAnimator(state, row.model.url);
        storeAnimator(state, result.animator);
      } catch (err) {
        console.warn(`[SceneManifest] Failed to load model '${row.id}':`, err);
      }
    } else if (row.audio?.url) {
      const eid = state.createEntity();
      state.addComponent(eid, AudioSource, {
        volume: 0.7,
        loop: 1,
        spatial: 0,
        playing: 1,
      });
      AudioSource.clipPath[eid] = eid;
      registerAudioClip(eid, basePath + row.audio.url);
    }

    if (row.pbr_textures?.length) {
      const channelOrder = [0, 1, 2, 3, 4];
      for (
        let i = 0;
        i < row.pbr_textures.length && i < channelOrder.length;
        i++
      ) {
        const eid = state.createEntity();
        state.addComponent(eid, TextureRecipe, {
          channel: channelOrder[i],
          pending: 0,
          repeatMode: 0,
          repeatX: 1,
          repeatY: 1,
          flipX: 0,
          flipY: 0,
          anisotropy: 0,
        });
        setTextureRecipeUrl(eid, basePath + row.pbr_textures[i]);
      }
    }
  }
}

export const HandoffAnimatorTickSystem: System = {
  group: 'draw',
  update(state: State) {
    const tracked = stateTrackedEntities.get(state);
    if (!tracked || tracked.size === 0) return;

    const dt = state.time.deltaTime;
    const removed: number[] = [];

    for (const eid of tracked) {
      if (!state.exists(eid)) {
        removed.push(eid);
        continue;
      }

      const animator = animatorMap.get(eid);
      if (animator) {
        animator.update(dt);
      }
    }

    for (const eid of removed) {
      const animator = animatorMap.get(eid);
      if (animator) {
        animator.dispose();
        animatorMap.delete(eid);
      }
      tracked.delete(eid);
    }
  },
};

export async function loadSceneManifest(
  state: State,
  url = '/assets/gameassets_handoff.json',
  basePath = '/'
): Promise<SceneManifest> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(
      `Failed to load manifest: ${response.status} ${response.statusText}`
    );
  }

  const data: RawManifest = await response.json();

  if (isHandoffFormat(data)) {
    await loadHandoffFormat(state, data, basePath);
  } else {
    await loadOldFormat(state, data as SceneManifest, basePath);
  }

  return data as SceneManifest;
}
