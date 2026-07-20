import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core';
import {
  Collider,
  ColliderShape,
} from '../../../src/plugins/physics/components';
import {
  getBodyYForFeetAt,
  getCharacterFeetY,
} from '../../../src/plugins/physics/character-ground';

describe('character-ground feet math', () => {
  let state: State;
  let eid: number;

  beforeEach(() => {
    state = new State();
    eid = state.createEntity();
    state.addComponent(eid, Collider);
  });

  it('capsule includes hemispheres (height/2 + radius)', () => {
    Collider.shape[eid] = ColliderShape.Capsule;
    Collider.height[eid] = 0.9;
    Collider.radius[eid] = 0.3;
    Collider.posOffsetY[eid] = 0.75;

    // Bottom of capsule = bodyY + offsetY - (h/2 + r) = bodyY
    expect(getCharacterFeetY(state, eid, 10)).toBeCloseTo(10, 5);
    expect(getBodyYForFeetAt(state, eid, 36.05)).toBeCloseTo(36.05, 5);
  });

  it('box uses sizeY/2', () => {
    Collider.shape[eid] = ColliderShape.Box;
    Collider.sizeY[eid] = 2;
    Collider.posOffsetY[eid] = 0;

    expect(getCharacterFeetY(state, eid, 5)).toBeCloseTo(4, 5);
    expect(getBodyYForFeetAt(state, eid, 4)).toBeCloseTo(5, 5);
  });
});
