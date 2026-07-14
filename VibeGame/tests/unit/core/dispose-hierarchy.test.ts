import { describe, expect, it } from 'bun:test';
import { Parent, State } from 'vibegame';

describe('State.dispose hierarchy', () => {
  it('invokes onDestroyAll once per entity when disposing a parent/child tree', () => {
    const state = new State();
    const seen: number[] = [];
    state.onDestroyAll((eid) => {
      seen.push(eid);
    });

    const parent = state.createEntity();
    const child = state.createEntity();
    state.addComponent(child, Parent, { entity: parent });

    state.dispose();

    expect(seen.filter((id) => id === parent)).toHaveLength(1);
    expect(seen.filter((id) => id === child)).toHaveLength(1);
    expect(seen).toHaveLength(2);
  });
});
