import { defineQuery, type State, type System } from '../../core';
import { COMBAT_DEATH, emitEvent } from '../rpg-core/events';
import { TouchedEvent } from '../physics/components';
import {
  Health,
  ProjectileConfig,
  ProjectileData,
  damageHealth,
  getDeathFlags,
} from './components';

const touchedProjectileQuery = defineQuery([TouchedEvent, ProjectileData]);
const projectileQuery = defineQuery([ProjectileData]);
const projectileConfigQuery = defineQuery([ProjectileConfig]);
const healthQuery = defineQuery([Health]);

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
    const configured = new Set(projectileConfigQuery(state.world));
    for (const eid of entities) {
      const newAge = ProjectileData.age[eid] + state.time.deltaTime;
      ProjectileData.age[eid] = newAge;
      const maxLife = configured.has(eid)
        ? ProjectileConfig.maxLife[eid]
        : ProjectileData.lifetime[eid];
      if (newAge >= maxLife) {
        state.destroyEntity(eid);
      }
    }
  },
};

export const CombatDeathCleanupSystem: System = {
  group: 'simulation',
  update(state: State): void {
    const entities = healthQuery(state.world);
    const deathEmitted = getDeathFlags(state);
    for (const eid of entities) {
      if (Health.current[eid] <= 0 && deathEmitted[eid] === 0) {
        deathEmitted[eid] = 1;
        emitEvent(state, COMBAT_DEATH, { target: eid });
      }
    }
  },
};
