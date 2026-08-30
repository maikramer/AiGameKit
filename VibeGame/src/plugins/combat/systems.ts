import { defineSystem, defineQuery, type State, type System } from '../../core';
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

// Hoisted per-frame scratch set for projectile-config membership checks
// (bitecs query arrays are cached; only the Set allocation is hot-path waste).
const _projectileConfigSet = new Set<number>();

export const DamageResolutionSystem: System = defineSystem({
  name: 'DamageResolutionSystem',
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
        damageHealth(other, damage, ownerEid);
      }

      state.destroyEntity(eid);
    }
  },
});

export const ProjectileCleanupSystem: System = defineSystem({
  name: 'ProjectileCleanupSystem',
  group: 'simulation',
  update(state: State): void {
    const entities = projectileQuery(state.world);
    _projectileConfigSet.clear();
    for (const e of projectileConfigQuery(state.world)) {
      _projectileConfigSet.add(e);
    }
    for (const eid of entities) {
      // Snapshot staleness: DamageResolutionSystem may have destroyed this
      // projectile earlier in the same frame — don't age/destroy dead slots
      // (a recycled eid would get its fresh state corrupted).
      if (!state.exists(eid)) continue;
      const newAge = ProjectileData.age[eid] + state.time.deltaTime;
      ProjectileData.age[eid] = newAge;
      const maxLife = _projectileConfigSet.has(eid)
        ? ProjectileConfig.maxLife[eid]
        : ProjectileData.lifetime[eid];
      if (newAge >= maxLife) {
        state.destroyEntity(eid);
      }
    }
  },
});

export const CombatDeathCleanupSystem: System = defineSystem({
  name: 'CombatDeathCleanupSystem',
  group: 'simulation',
  update(state: State): void {
    const entities = healthQuery(state.world);
    const deathEmitted = getDeathFlags(state);
    for (const eid of entities) {
      // A same-frame COMBAT_DAMAGED listener may have destroyed the victim;
      // a cleared slot reads current=0/flag=0 and would emit a death event
      // for an entity that no longer exists.
      if (!state.exists(eid)) continue;
      if (Health.current[eid] <= 0 && deathEmitted[eid] === 0) {
        deathEmitted[eid] = 1;
        emitEvent(state, COMBAT_DEATH, { target: eid });
      }
    }
  },
});

/** Countdown for `Health.invulnTimer` (i-frames granted by games on hit). */
export const CombatInvulnSystem: System = defineSystem({
  name: 'CombatInvulnSystem',
  group: 'simulation',
  update(state: State): void {
    const dt = state.time.deltaTime;
    for (const eid of healthQuery(state.world)) {
      if (!state.exists(eid)) continue; // snapshot may hold same-frame dead
      if (Health.invulnTimer[eid] <= 0) continue;
      Health.invulnTimer[eid] = Math.max(0, Health.invulnTimer[eid] - dt);
    }
  },
});
