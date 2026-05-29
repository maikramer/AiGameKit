import type { Plugin } from '../../core';
import { BvhTarget } from './components';
import { BvhStaticMeshSyncSystem, BvhTerrainSyncSystem } from './systems';

export const BvhPlugin: Plugin = {
  systems: [BvhTerrainSyncSystem, BvhStaticMeshSyncSystem],
  components: {
    BvhTarget,
  },
  config: {
    defaults: {
      'bvh-target': {
        include: 1,
        layer: 0xffff,
        dirty: 1,
      },
    },
  },
};
