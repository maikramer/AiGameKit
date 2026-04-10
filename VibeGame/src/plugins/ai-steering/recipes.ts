import type { Recipe } from '../../core';

/** Default visible placeholder (instanced sphere) when no mesh is specified. */
export const npcRecipe: Recipe = {
  name: 'npc',
  merge: true,
  components: ['transform', 'steeringAgent', 'steeringTarget', 'renderer'],
  overrides: {
    'renderer.shape': 1,
    'renderer.sizeX': 0.44,
    'renderer.sizeY': 0.88,
    'renderer.sizeZ': 0.44,
    'renderer.color': 0x7e57c2,
    'renderer.visible': 1,
    'renderer.unlit': 0,
  },
};
