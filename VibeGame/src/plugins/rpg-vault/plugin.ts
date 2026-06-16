import type { Plugin, Recipe } from '../../core';
import {
  VaultComponent,
  registerResourceKind,
  setCapacity,
} from './components';
import { VaultEventBridgeSystem } from './systems';

const CAPACITY_PREFIX = 'capacity-';

const vaultRecipe: Recipe = {
  name: 'Vault',
  components: ['vault'],
};

export const RpgVaultPlugin: Plugin = {
  systems: [VaultEventBridgeSystem],
  recipes: [vaultRecipe],
  components: { vault: VaultComponent },
  config: {
    defaults: {
      vault: { active: 1 },
    },
    parsers: {
      Vault: ({ entity, element, state }) => {
        for (const [key, raw] of Object.entries(element.attributes)) {
          if (!key.startsWith(CAPACITY_PREFIX)) continue;
          const kind = key.slice(CAPACITY_PREFIX.length);
          if (kind.length === 0) continue;
          const value = Number(raw);
          if (Number.isNaN(value)) continue;
          registerResourceKind(state, kind);
          setCapacity(state, entity, kind, value);
        }
      },
    },
  },
};
