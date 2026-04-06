import type { Plugin } from '../../core';
import { GltfAnimationState } from './components';
import { GltfAnimationUpdateSystem } from './systems';

export const GltfAnimPlugin: Plugin = {
  systems: [GltfAnimationUpdateSystem],
  components: {
    GltfAnimationState,
  },
  config: {
    defaults: {
      'gltf-animation-state': {
        registryIndex: 0,
        activeClipIndex: 0,
        isPlaying: 0,
        crossfadeDuration: 0.25,
      },
    },
  },
};
