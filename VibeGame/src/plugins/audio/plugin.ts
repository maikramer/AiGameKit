import type { Plugin } from '../../core';
import { AudioEmitter, AudioListener } from './components';
import { AudioSystem } from './systems';

export const AudioPlugin: Plugin = {
  systems: [AudioSystem],
  components: {
    AudioEmitter,
    AudioListener,
  },
  config: {
    defaults: {
      audioEmitter: {
        volume: 1,
        loop: 0,
        pitch: 1,
        spatial: 1,
        minDistance: 1,
        maxDistance: 100,
        rolloff: 1,
        playing: 0,
      },
    },
  },
};
