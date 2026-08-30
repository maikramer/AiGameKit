import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import {
  PhysicsInterpolationSystem,
  PhysicsRapierSyncSystem,
} from '../../../src/plugins/physics/physics-sync';

describe('physics-sync', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('PhysicsInterpolationSystem runs without throw on empty state', () => {
    expect(() => PhysicsInterpolationSystem.update?.(state)).not.toThrow();
  });

  it('PhysicsRapierSyncSystem no-ops with empty physics context', () => {
    expect(() => PhysicsRapierSyncSystem.update?.(state)).not.toThrow();
  });
});
