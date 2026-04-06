import type { Plugin } from '../../core';
import { Sprite } from './components';
import { SpriteSystem } from './systems';

export const SpritePlugin: Plugin = {
  systems: [SpriteSystem],
  components: {
    Sprite,
  },
  config: {
    defaults: {
      sprite: {
        width: 1,
        height: 1,
        pivotX: 0.5,
        pivotY: 0.5,
        opacity: 1,
        colorR: 1,
        colorG: 1,
        colorB: 1,
        flipX: 0,
        flipY: 0,
        layer: 0,
      },
    },
  },
};
