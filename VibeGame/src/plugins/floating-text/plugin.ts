import type { Plugin } from '../../core';
import { FloatingText } from './components';
import { FloatingTextUpdateSystem } from './systems';

export const FloatingTextPlugin: Plugin = {
  systems: [FloatingTextUpdateSystem],
  components: { 'floating-text': FloatingText },
  config: {
    defaults: {
      'floating-text': {
        elapsed: 0,
        duration: 1.4,
        riseSpeed: 0.9,
        size: 0.35,
        colorR: 1,
        colorG: 1,
        colorB: 1,
      },
    },
  },
};
