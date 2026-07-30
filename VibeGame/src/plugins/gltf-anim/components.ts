import { MAX_ENTITIES } from '../../core/ecs/constants';

export const GltfAnimationState = {
  registryIndex: new Uint32Array(MAX_ENTITIES),
  activeClipIndex: new Uint8Array(MAX_ENTITIES),
  isPlaying: new Uint8Array(MAX_ENTITIES),
  crossfadeDuration: new Float32Array(MAX_ENTITIES),
  /**
   * Opt-in root motion: 1 means the animator's root object carries the
   * entity's **world** pose, so the update system copies it back into
   * `WorldTransform`.
   *
   * Off by default because the common case is the opposite: an animator
   * attached to a GLB that a `<GLTFLoader>` already placed drives a root whose
   * transform is *local to that GLB group* — usually a plain identity. Copying
   * that into `WorldTransform` teleports the entity to the world origin every
   * frame, and re-dirties `Transform` so the hierarchy fights it forever.
   */
  rootMotion: new Uint8Array(MAX_ENTITIES),
} as const;
