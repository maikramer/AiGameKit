import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import type { Plugin, System } from 'vibegame';
import { RaycastPlugin } from '../../../src/plugins/raycast/plugin';

describe('State.registerPlugin dedupe', () => {
  it('skips re-registering the same plugin instance', () => {
    const state = new State();
    const marker: System = {
      group: 'simulation',
      update: () => {},
    };
    const plugin: Plugin = {
      systems: [marker],
      components: { dedupeMarker: { value: new Float32Array(1) } },
    };

    state.registerPlugin(plugin);
    state.registerPlugin(plugin);

    expect(state.getComponent('dedupe-marker')).toBeDefined();
    expect([...state.systems].filter((s) => s === marker)).toHaveLength(1);
  });

  it('does not duplicate default plugin systems on repeated register', () => {
    const state = new State();
    state.registerPlugin(RaycastPlugin);
    const countAfterFirst = state.systems.size;
    state.registerPlugin(RaycastPlugin);
    expect(state.systems.size).toBe(countAfterFirst);
  });
});
