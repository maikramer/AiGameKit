import type { Recipe } from '../../core';

/** Default visible placeholder (instanced sphere) when no mesh is specified. */
export const npcRecipe: Recipe = {
  name: 'NPC',
  merge: true,
  components: ['transform', 'steeringAgent', 'steeringTarget', 'meshRenderer'],
  overrides: {
    'meshRenderer.shape': 1,
    'meshRenderer.sizeX': 0.44,
    'meshRenderer.sizeY': 0.88,
    'meshRenderer.sizeZ': 0.44,
    'meshRenderer.color': 0x7e57c2,
    'meshRenderer.visible': 1,
    'meshRenderer.unlit': 0,
  },
};
