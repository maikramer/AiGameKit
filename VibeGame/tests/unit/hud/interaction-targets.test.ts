import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import { TransformsPlugin } from '../../../src/plugins/transforms';
import { Transform } from '../../../src/plugins/transforms';
import {
  findNearestInteractionTarget,
  normalizePromptKey,
  registerInteractionTarget,
  resolveInteractionGesture,
  unregisterInteractionTarget,
} from '../../../src/plugins/hud/interaction-targets';

function freshState(): State {
  const state = new State();
  state.registerPlugin(TransformsPlugin);
  return state;
}

function placeEntity(state: State, x: number, z: number): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  Transform.posX[eid] = x;
  Transform.posY[eid] = 0;
  Transform.posZ[eid] = z;
  return eid;
}

describe('interaction-targets gesture helpers', () => {
  it('normalizePromptKey maps KeyF / f / F', () => {
    expect(normalizePromptKey('KeyF')).toBe('F');
    expect(normalizePromptKey('f')).toBe('F');
    expect(normalizePromptKey('F')).toBe('F');
    expect(normalizePromptKey('KeyK')).toBe('K');
  });

  it('resolveInteractionGesture defaults to none', () => {
    expect(resolveInteractionGesture(undefined)).toBe('none');
    expect(resolveInteractionGesture({})).toBe('none');
    expect(resolveInteractionGesture({ gesture: 'none' })).toBe('none');
    expect(resolveInteractionGesture({ gesture: 'gather' })).toBe('gather');
  });

  it('findNearestInteractionTarget prefers nearer F target and respects gesture', () => {
    const state = freshState();

    const chest = placeEntity(state, 2, 0);
    registerInteractionTarget(state, chest, {
      label: 'Abrir baú',
      key: 'F',
    });

    const mush = placeEntity(state, 1, 0);
    registerInteractionTarget(state, mush, {
      label: 'Comer cogumelo',
      key: 'F',
      gesture: 'gather',
    });

    const nearest = findNearestInteractionTarget(state, 0, 0, { key: 'F' });
    expect(nearest?.eid).toBe(mush);
    expect(resolveInteractionGesture(nearest?.info)).toBe('gather');

    unregisterInteractionTarget(state, mush);
    const after = findNearestInteractionTarget(state, 0, 0, { key: 'F' });
    expect(after?.eid).toBe(chest);
    expect(resolveInteractionGesture(after?.info)).toBe('none');
  });

  it('findNearestInteractionTarget ignores other keys', () => {
    const state = freshState();
    const merchant = placeEntity(state, 1, 0);
    registerInteractionTarget(state, merchant, {
      label: 'Comerciar',
      key: 'K',
    });

    expect(findNearestInteractionTarget(state, 0, 0, { key: 'F' })).toBeNull();
    expect(findNearestInteractionTarget(state, 0, 0, { key: 'K' })?.eid).toBe(
      merchant
    );
  });
});
