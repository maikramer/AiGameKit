import type { Plugin } from '../../core';
import { HandoffAnimatorTickSystem } from './loader';

export interface SceneManifestPluginConfig {
  manifestUrl?: string;
  basePath?: string;
}

export function createSceneManifestConfig(
  config?: SceneManifestPluginConfig
): Required<SceneManifestPluginConfig> {
  return {
    manifestUrl: config?.manifestUrl ?? '/assets/gameassets_handoff.json',
    basePath: config?.basePath ?? '/',
  };
}

export const SceneManifestPlugin: Plugin = {
  systems: [HandoffAnimatorTickSystem],
  components: {},
};
