import { describe, expect, it } from 'bun:test';
import { State, createSnapshot, restoreSnapshot } from 'vibegame';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';

describe('snapshot with Float64Array fields', () => {
  it('serializes and restores Float64Array component fields (xp/respawn timers)', () => {
    const state = new State();
    const Xp = { value: new Float64Array(MAX_ENTITIES) };
    state.registerComponent('xp', Xp);

    const eid = state.createEntity();
    state.addComponent(eid, Xp);
    Xp.value[eid] = 1234.5;

    const snap = createSnapshot(state);
    // The xp field must be captured (the old typed-array whitelist skipped
    // Float64Array entirely, silently losing XP and respawn timers).
    const fields = snap.entities[0]!.components.xp as Record<string, number>;
    expect(fields.value).toBe(1234.5);

    const restored = new State();
    restored.registerComponent('xp', Xp);
    const result = restoreSnapshot(restored, snap);
    expect(result.restoredCount).toBe(1);
    const newEid = result.oldToNewEid.get(eid)!;
    expect(Xp.value[newEid]).toBe(1234.5);
  });

  it('omits Float64Array fields for entities without the component', () => {
    const state = new State();
    const Other = { n: new Float32Array(MAX_ENTITIES) };
    state.registerComponent('other', Other);
    const eid = state.createEntity();
    state.addComponent(eid, Other);
    Other.n[eid] = 7;
    const snap = createSnapshot(state);
    const fields = snap.entities[0]!.components.other as Record<string, number>;
    expect(fields.n).toBe(7);
    expect(
      (snap.entities[0]!.components as Record<string, unknown>).xp
    ).toBeUndefined();
  });
});
