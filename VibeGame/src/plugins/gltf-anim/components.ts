import { MAX_ENTITIES } from '../../core/ecs/constants';

export const GltfAnimationState = {
  registryIndex: new Uint32Array(MAX_ENTITIES),
  activeClipIndex: new Uint8Array(MAX_ENTITIES),
  isPlaying: new Uint8Array(MAX_ENTITIES),
  crossfadeDuration: new Float32Array(MAX_ENTITIES),
} as const;
