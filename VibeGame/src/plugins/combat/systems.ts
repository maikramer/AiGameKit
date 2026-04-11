import { defineQuery, type State, type System } from '../../core';

import { TouchedEvent } from '../physics/components';
import { ProjectileData, Health, damageHealth } from './components';

const touchedProjectileQuery = defineQuery([TouchedEvent, ProjectileData]);
const projectileQuery = defineQuery([ProjectileData]);

export const DamageResolutionSystem: System = {
  group: 'simulation',
  update(state: State): void {
    const entities = touchedProjectileQuery(state.world);
    for (const eid of entities) {
      const other = TouchedEvent.other[eid];
      const ownerEid = ProjectileData.ownerEid[eid];

      if (other === ownerEid) {
        state.destroyEntity(eid);
        continue;
      }

      if (state.hasComponent(other, Health)) {
        const damage = ProjectileData.damage[eid];
        damageHealth(other, damage);
      }

      state.destroyEntity(eid);
    }
  },
};

export const ProjectileCleanupSystem: System = {
  group: 'simulation',
  update(state: State): void {
    const entities = projectileQuery(state.world);
    for (const eid of entities) {
      const newAge = ProjectileData.age[eid] + state.time.deltaTime;
      ProjectileData.age[eid] = newAge;

      if (newAge >= ProjectileData.lifetime[eid]) {
        state.destroyEntity(eid);
      }
    }
  },
};
