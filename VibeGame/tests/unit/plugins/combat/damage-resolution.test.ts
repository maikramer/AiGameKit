import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import {
  Health,
  ProjectileData,
} from '../../../../src/plugins/combat/components';
import { DamageResolutionSystem } from '../../../../src/plugins/combat/systems';
import { TouchedEvent } from '../../../../src/plugins/physics/components';

describe('DamageResolutionSystem', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('touchedEvent', TouchedEvent);
    state.registerComponent('projectileData', ProjectileData);
    state.registerComponent('health', Health);
  });

  function createProjectile(
    ownerEid: number,
    damage: number,
    targetEid: number
  ): number {
    const eid = state.createEntity();
    state.addComponent(eid, ProjectileData);
    ProjectileData.damage[eid] = damage;
    ProjectileData.ownerEid[eid] = ownerEid;
    ProjectileData.lifetime[eid] = 5;
    ProjectileData.age[eid] = 0;
    state.addComponent(eid, TouchedEvent);
    TouchedEvent.other[eid] = targetEid;
    return eid;
  }

  function createTarget(hp: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, Health);
    Health.current[eid] = hp;
    Health.max[eid] = hp;
    return eid;
  }

  function run(): void {
    DamageResolutionSystem.update!(state);
  }

  it('should apply damage to target with Health and destroy projectile', () => {
    const target = createTarget(100);
    const projectile = createProjectile(999, 25, target);

    run();

    expect(Health.current[target]).toBe(75);
    expect(state.hasComponent(projectile, ProjectileData)).toBe(false);
  });

  it('should destroy projectile when hitting entity without Health', () => {
    const nakedEid = state.createEntity();
    const projectile = createProjectile(999, 25, nakedEid);

    run();

    expect(state.hasComponent(projectile, ProjectileData)).toBe(false);
  });

  it('should not damage self (friendly fire check)', () => {
    const owner = createTarget(100);
    const projectile = createProjectile(owner, 50, owner);

    run();

    expect(Health.current[owner]).toBe(100);
    expect(state.hasComponent(projectile, ProjectileData)).toBe(false);
  });

  it('should accumulate damage from multiple projectiles hitting same target', () => {
    const target = createTarget(100);
    const p1 = createProjectile(999, 30, target);
    const p2 = createProjectile(999, 40, target);

    run();

    expect(Health.current[target]).toBe(30);
    expect(state.hasComponent(p1, ProjectileData)).toBe(false);
    expect(state.hasComponent(p2, ProjectileData)).toBe(false);
  });
});
