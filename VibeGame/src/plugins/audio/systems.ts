import type { System } from '../../core';

export const AudioSystem: System = {
  group: 'simulation',
  // No-op: actual audio playback is handled by the runtime
  // when AudioEmitter.playing transitions 0→1.
};
