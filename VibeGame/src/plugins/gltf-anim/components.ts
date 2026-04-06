import { defineComponent, Types } from 'bitecs';

export const GltfAnimationState = defineComponent({
  registryIndex: Types.ui32,
  activeClipIndex: Types.ui8,
  isPlaying: Types.ui8,
  crossfadeDuration: Types.f32,
});
