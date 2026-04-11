import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  ProjectileData,
  setProjectileOwner,
  incrementProjectileAge,
  isProjectileExpired,
} from '../../../../src/plugins/combat/components';

describe('ProjectileData Component', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    eid = state.createEntity();
    state.addComponent(eid, ProjectileData);
    ProjectileData.damage[eid] = 10;
    ProjectileData.ownerEid[eid] = 0;
    ProjectileData.lifetime[eid] = 3.0;
    ProjectileData.age[eid] = 0;
  });

  it('should have correct default structure', () => {
    expect(ProjectileData.damage).toBeDefined();
    expect(ProjectileData.ownerEid).toBeDefined();
    expect(ProjectileData.lifetime).toBeDefined();
    expect(ProjectileData.age).toBeDefined();
  });

  it('should have default values set correctly', () => {
    expect(ProjectileData.damage[eid]).toBe(10);
    expect(ProjectileData.ownerEid[eid]).toBe(0);
    expect(ProjectileData.lifetime[eid]).toBe(3.0);
    expect(ProjectileData.age[eid]).toBe(0);
  });

  it('setProjectileOwner updates ownerEid', () => {
    setProjectileOwner(eid, 42);
    expect(ProjectileData.ownerEid[eid]).toBe(42);
  });

  it('incrementProjectileAge increases age by dt', () => {
    incrementProjectileAge(eid, 0.5);
    expect(ProjectileData.age[eid]).toBe(0.5);
    incrementProjectileAge(eid, 1.0);
    expect(ProjectileData.age[eid]).toBe(1.5);
  });

  it('isProjectileExpired returns false before lifetime', () => {
    incrementProjectileAge(eid, 2.9);
    expect(isProjectileExpired(eid)).toBe(false);
  });

  it('isProjectileExpired returns true at or after lifetime', () => {
    incrementProjectileAge(eid, 3.0);
    expect(isProjectileExpired(eid)).toBe(true);

    incrementProjectileAge(eid, 0.1);
    expect(isProjectileExpired(eid)).toBe(true);
  });
});
