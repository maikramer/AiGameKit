import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  Collider,
  ColliderShape,
  GROUND_CONTACT_SKIN,
  getBodyYForFeetAt,
  getCharacterFeetY,
} from 'vibegame/physics';

describe('physics character-ground table-driven', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
    state.addComponent(entity, Collider);
  });

  it('GROUND_CONTACT_SKIN is a small positive margin', () => {
    expect(GROUND_CONTACT_SKIN).toBeCloseTo(0.05, 5);
  });

  for (let i = 0; i < 50; i++) {
    it(`box feet/body roundtrip case ${i}`, () => {
      Collider.shape[entity] = ColliderShape.Box;
      Collider.sizeY[entity] = 1 + (i % 10) * 0.2;
      Collider.posOffsetY[entity] = (i % 5) * 0.03;
      const bodyY = 3 + i * 0.11;
      const feet = getCharacterFeetY(state, entity, bodyY);
      const back = getBodyYForFeetAt(state, entity, feet);
      expect(back).toBeCloseTo(bodyY, 5);
      const half = Collider.sizeY[entity] / 2;
      expect(feet).toBeCloseTo(bodyY - half + Collider.posOffsetY[entity], 5);
    });
  }

  for (let i = 0; i < 50; i++) {
    it(`capsule feet/body roundtrip case ${i}`, () => {
      Collider.shape[entity] = ColliderShape.Capsule;
      Collider.height[entity] = 1.2 + (i % 8) * 0.15;
      Collider.radius[entity] = 0.25 + (i % 4) * 0.05;
      Collider.posOffsetY[entity] = -0.02 + (i % 3) * 0.01;
      const bodyY = 1.5 + i * 0.07;
      const feet = getCharacterFeetY(state, entity, bodyY);
      const back = getBodyYForFeetAt(state, entity, feet);
      expect(back).toBeCloseTo(bodyY, 5);
      const half = Collider.height[entity] / 2 + Collider.radius[entity];
      expect(feet).toBeCloseTo(bodyY - half + Collider.posOffsetY[entity], 5);
    });
  }
});
