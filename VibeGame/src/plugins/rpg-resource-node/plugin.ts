import type { Plugin } from '../../core';
import { ResourceNode } from './components';
import { resourceNodeRecipe } from './recipes';
import { ResourceNodeRespawnSystem } from './systems';
import { resolveResourceNodeKind } from './utils';

export const ResourceNodePlugin: Plugin = {
  systems: [ResourceNodeRespawnSystem],
  components: { 'resource-node': ResourceNode },
  recipes: [resourceNodeRecipe],
  config: {
    defaults: {
      'resource-node': {
        kind: 0,
        yield: 1,
        respawn: 0,
        depleted: 0,
        respawnAt: 0,
      },
    },
    enums: {
      'resource-node': {
        kind: {
          wood: 0,
          stone: 1,
          ore: 2,
        },
      },
    },
    parsers: {
      ResourceNode: ({ entity, element, state }) => {
        const raw = element.attributes.kind;
        if (raw === undefined || raw === null) return;
        const value = String(raw).trim();
        if (value.length === 0) return;
        ResourceNode.kind[entity] = resolveResourceNodeKind(state, value);
      },
    },
  },
};
