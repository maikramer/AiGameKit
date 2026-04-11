import type { Plugin } from '../../core';
import { Health, ProjectileData } from './components';

export const CombatPlugin: Plugin = {
  systems: [],
  components: {
    health: Health,
    projectileData: ProjectileData,
  },
  config: {
    defaults: {
      health: { current: 100, max: 100 },
      projectileData: { damage: 10, ownerEid: 0, lifetime: 3.0, age: 0 },
    },
  },
};
