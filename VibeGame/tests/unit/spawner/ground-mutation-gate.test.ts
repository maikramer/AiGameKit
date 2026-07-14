import { describe, expect, it, beforeEach } from 'bun:test';
import { State } from 'vibegame';
import { TerrainPad } from '../../../src/plugins/terrain/components';
import { Lake, River } from '../../../src/plugins/water/components';
import { Road } from '../../../src/plugins/road/components';
import { isGroundMutationPending } from '../../../src/plugins/spawner/surface';

describe('isGroundMutationPending', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('terrainPad', TerrainPad);
    state.registerComponent('lake', Lake);
    state.registerComponent('river', River);
    state.registerComponent('road', Road);
  });

  it('false num mundo sem pads nem carves', () => {
    expect(isGroundMutationPending(state)).toBe(false);
  });

  it('true enquanto um TerrainPad não aplicou; false depois', () => {
    const eid = state.createEntity();
    state.addComponent(eid, TerrainPad);
    TerrainPad.applied[eid] = 0;
    expect(isGroundMutationPending(state)).toBe(true);

    TerrainPad.applied[eid] = 1;
    expect(isGroundMutationPending(state)).toBe(false);
  });

  it('true enquanto um Lake não esculpiu; false depois', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Lake);
    Lake.applied[eid] = 0;
    expect(isGroundMutationPending(state)).toBe(true);

    Lake.applied[eid] = 1;
    expect(isGroundMutationPending(state)).toBe(false);
  });

  it('true enquanto um River não esculpiu; false depois', () => {
    const eid = state.createEntity();
    state.addComponent(eid, River);
    River.applied[eid] = 0;
    expect(isGroundMutationPending(state)).toBe(true);

    River.applied[eid] = 1;
    expect(isGroundMutationPending(state)).toBe(false);
  });

  it('true enquanto um Road com flatten não aplicou; false depois', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Road);
    Road.flatten[eid] = 1;
    Road.applied[eid] = 0;
    expect(isGroundMutationPending(state)).toBe(true);

    Road.applied[eid] = 1;
    expect(isGroundMutationPending(state)).toBe(false);
  });

  it('Road sem flatten não bloqueia spawn', () => {
    const eid = state.createEntity();
    state.addComponent(eid, Road);
    Road.flatten[eid] = 0;
    Road.applied[eid] = 0;
    expect(isGroundMutationPending(state)).toBe(false);
  });

  it('mistura: basta um pendente para bloquear', () => {
    const pad = state.createEntity();
    state.addComponent(pad, TerrainPad);
    TerrainPad.applied[pad] = 1;

    const river = state.createEntity();
    state.addComponent(river, River);
    River.applied[river] = 0;

    expect(isGroundMutationPending(state)).toBe(true);
    River.applied[river] = 1;
    expect(isGroundMutationPending(state)).toBe(false);
  });
});
