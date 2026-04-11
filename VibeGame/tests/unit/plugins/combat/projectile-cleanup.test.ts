import { beforeEach, describe, expect, it } from 'bun:test';

import { State } from '../../../../src/core/ecs/state';
import { ProjectileData } from '../../../../src/plugins/combat/components';
import { ProjectileCleanupSystem } from '../../../../src/plugins/combat/systems';

describe('ProjectileCleanupSystem', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('projectileData', ProjectileData);
  });

  function createProjectile(age: number, lifetime: number): number {
    const eid = state.createEntity();
    state.addComponent(eid, ProjectileData);
    ProjectileData.age[eid] = age;
    ProjectileData.lifetime[eid] = lifetime;
    ProjectileData.damage[eid] = 10;
    ProjectileData.ownerEid[eid] = 0;
    return eid;
  }

  function run(deltaTime: number = 1): void {
    Object.defineProperty(state.time, 'deltaTime', {
      value: deltaTime,
      writable: true,
    });
    ProjectileCleanupSystem.update!(state);
  }

  it('should not destroy projectile with age < lifetime', () => {
    const projectile = createProjectile(0, 3);

    run(1);

    expect(ProjectileData.age[projectile]).toBe(1);
    expect(state.hasComponent(projectile, ProjectileData)).toBe(true);
  });

  it('should destroy projectile with age >= lifetime', () => {
    const projectile = createProjectile(2.5, 3);

    run(0.5);

    expect(state.hasComponent(projectile, ProjectileData)).toBe(false);
  });

  it('should only destroy expired projectiles', () => {
    const alive = createProjectile(0, 5);
    const expired = createProjectile(2.9, 3);

    run(0.1);

    expect(state.hasComponent(alive, ProjectileData)).toBe(true);
    expect(state.hasComponent(expired, ProjectileData)).toBe(false);
  });

  it('should keep new projectile alive after 2.9s delta', () => {
    const projectile = createProjectile(0, 3);

    run(2.9);

    expect(ProjectileData.age[projectile]).toBeCloseTo(2.9, 5);
    expect(state.hasComponent(projectile, ProjectileData)).toBe(true);
  });
});
