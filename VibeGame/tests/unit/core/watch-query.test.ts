import { beforeEach, describe, expect, it } from 'bun:test';
import { State, watchQuery } from 'vibegame';

const MAX_ENTITIES = 100000;

const MarkerA = { value: new Float32Array(MAX_ENTITIES) };
const MarkerB = { value: new Float32Array(MAX_ENTITIES) };

describe('watchQuery', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
    state.registerComponent('marker-a', MarkerA);
    state.registerComponent('marker-b', MarkerB);
  });

  it('fires onAdded when an entity starts matching the query', () => {
    const added: number[] = [];
    watchQuery(state, [MarkerA, MarkerB], {
      onAdded: (eid) => added.push(eid),
    });

    const eid = state.createEntity();
    state.addComponent(eid, MarkerA);
    state.step(0.016);
    expect(added.length).toBe(0);

    state.addComponent(eid, MarkerB);
    state.step(0.016);
    expect(added).toEqual([eid]);
  });

  it('fires onRemoved when a component is lost or the entity is destroyed', () => {
    const removed: number[] = [];
    watchQuery(state, [MarkerA, MarkerB], {
      onRemoved: (eid) => removed.push(eid),
    });

    const eid = state.createEntity();
    state.addComponent(eid, MarkerA);
    state.addComponent(eid, MarkerB);
    state.step(0.016);

    state.removeComponent(eid, MarkerA);
    state.step(0.016);
    expect(removed).toEqual([eid]);

    // Re-match, then destroy: destroy must also fire onRemoved.
    state.addComponent(eid, MarkerA);
    state.step(0.016);
    removed.length = 0;

    state.destroyEntity(eid);
    state.step(0.016);
    expect(removed).toEqual([eid]);
  });

  it('current() lists matching entities', () => {
    const handle = watchQuery(state, [MarkerA], {});
    const eid = state.createEntity();
    state.addComponent(eid, MarkerA);
    state.step(0.016);

    expect(handle.current()).toContain(eid);
  });

  it('dispose stops callbacks', () => {
    const added: number[] = [];
    const handle = watchQuery(state, [MarkerA], {
      onAdded: (eid) => added.push(eid),
    });

    const eid = state.createEntity();
    state.addComponent(eid, MarkerA);
    state.step(0.016);
    expect(added.length).toBe(1);

    handle.dispose();
    const other = state.createEntity();
    state.addComponent(other, MarkerA);
    state.step(0.016);
    expect(added.length).toBe(1);
  });

  it('a throwing handler does not break the flush loop', () => {
    const added: number[] = [];
    watchQuery(state, [MarkerA], {
      onAdded: () => {
        throw new Error('boom');
      },
    });
    watchQuery(state, [MarkerA], { onAdded: (eid) => added.push(eid) });

    const eid = state.createEntity();
    state.addComponent(eid, MarkerA);
    state.step(0.016);
    expect(added).toEqual([eid]);
  });
});
