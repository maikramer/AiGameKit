import type { Plugin } from '../../core';
import { Health, ProjectileData } from './components';
import { DamageResolutionSystem, ProjectileCleanupSystem } from './systems';

export const CombatPlugin: Plugin = {
  systems: [DamageResolutionSystem, ProjectileCleanupSystem],
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
