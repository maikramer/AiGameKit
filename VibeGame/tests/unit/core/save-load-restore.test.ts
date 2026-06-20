import { describe, expect, it } from 'bun:test';
import {
  Parent,
  SaveLoadPlugin,
  Serializable,
  State,
  loadSnapshot,
  saveSnapshot,
} from 'vibegame';

function freshState(): State {
  const state = new State();
  state.headless = true;
  state.registerPlugin(SaveLoadPlugin);
  return state;
}

describe('save/load Parent.entity remap (C1)', () => {
  it('remaps Parent.entity to the restored parent eid (not the stale source eid)', () => {
    const state1 = freshState();

    const p1 = state1.createEntity();
    state1.setEntityName('parent', p1);
    state1.addComponent(p1, Serializable);
    Serializable.flag[p1] = 1;

    const c1 = state1.createEntity();
    state1.setEntityName('child', c1);
    state1.addComponent(c1, Serializable);
    Serializable.flag[c1] = 1;
    state1.addComponent(c1, Parent, { entity: p1 });

    expect(Parent.entity[c1]).toBe(p1);

    const data = saveSnapshot(state1);

    const state2 = freshState();
    state2.createEntity();

    loadSnapshot(state2, data);

    const p2 = state2.getEntityByName('parent');
    const c2 = state2.getEntityByName('child');
    expect(p2).not.toBeNull();
    expect(c2).not.toBeNull();

    expect(state2.hasComponent(c2!, Parent)).toBe(true);
    expect(Parent.entity[c2!]).toBe(p2!);
    expect(Parent.entity[c2!]).not.toBe(p1);
  });

  it('preserves a multi-level parent chain through save/load', () => {
    const state1 = freshState();

    const root = state1.createEntity();
    state1.setEntityName('root', root);
    state1.addComponent(root, Serializable);
    Serializable.flag[root] = 1;

    const mid = state1.createEntity();
    state1.setEntityName('mid', mid);
    state1.addComponent(mid, Serializable);
    Serializable.flag[mid] = 1;
    state1.addComponent(mid, Parent, { entity: root });

    const leaf = state1.createEntity();
    state1.setEntityName('leaf', leaf);
    state1.addComponent(leaf, Serializable);
    Serializable.flag[leaf] = 1;
    state1.addComponent(leaf, Parent, { entity: mid });

    const data = saveSnapshot(state1);

    const state2 = freshState();
    state2.createEntity();

    loadSnapshot(state2, data);

    const root2 = state2.getEntityByName('root')!;
    const mid2 = state2.getEntityByName('mid')!;
    const leaf2 = state2.getEntityByName('leaf')!;

    expect(Parent.entity[mid2]).toBe(root2);
    expect(Parent.entity[leaf2]).toBe(mid2);
  });

  it('leaves Parent unset on restored entities that had no parent', () => {
    const state1 = freshState();

    const orphan = state1.createEntity();
    state1.setEntityName('orphan', orphan);
    state1.addComponent(orphan, Serializable);
    Serializable.flag[orphan] = 1;

    const data = saveSnapshot(state1);

    const state2 = freshState();
    loadSnapshot(state2, data);

    const orphan2 = state2.getEntityByName('orphan')!;
    expect(state2.hasComponent(orphan2, Parent)).toBe(false);
  });
});
