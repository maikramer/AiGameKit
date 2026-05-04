import { defineComponent, Types } from 'bitecs';

export const ProfilerStats = defineComponent({
  lastFPS: Types.f32,
  frameTimeMs: Types.f32,
  systemCount: Types.ui32,
});
