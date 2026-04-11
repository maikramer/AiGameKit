import type { Plugin } from '../../core';
import { LODGroup } from './components';
import { LodSystem } from './systems';

export const LodPlugin: Plugin = {
  systems: [LodSystem],
  components: {
    lod: LODGroup,
  },
  config: {
    defaults: {
      lod: {
        near: 0,
        far: 50,
        currentLevel: 0,
        nearEntity: 0,
        farEntity: 0,
      },
    },
  },
};
