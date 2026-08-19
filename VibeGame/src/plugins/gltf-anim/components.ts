import {
  defineComponent,
  F32,
  U32,
  U8,
} from '../../core/ecs/component-storage';

export const GltfAnimationState = defineComponent({
  registryIndex: U32,
  activeClipIndex: U8,
  isPlaying: U8,
  crossfadeDuration: F32,
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
  rootMotion: U8,
});
