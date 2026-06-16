import type { Plugin } from '../../core';
import { FloatingText } from './components';
import {
  FloatingTextScreenUpdateSystem,
  FloatingTextUpdateSystem,
} from './systems';

export const FloatingTextPlugin: Plugin = {
  systems: [FloatingTextUpdateSystem, FloatingTextScreenUpdateSystem],
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
        space: 0,
        screenX: 0,
        screenY: 0,
        fontSizePx: 0,
        driftX: 0,
        crit: 0,
      },
    },
  },
};
