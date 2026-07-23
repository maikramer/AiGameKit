import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  claimStackSlot,
  clearFloatingTextStacks,
} from '../../../src/plugins/floating-text/stacking';

describe('floating-text stacking table-driven', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    clearFloatingTextStacks(state);
  });

  for (let depth = 1; depth <= 20; depth++) {
    for (const gap of [0.25, 0.5, 0.75]) {
      it(`stack depth ${depth} gap ${gap}`, () => {
        const key = `d${depth}g${gap}`;
        let lastOffset = -gap;
        for (let n = 0; n < depth; n++) {
          const eid = state.createEntity();
          const slot = claimStackSlot(state, key, eid, 10, gap);
          expect(slot.yOffset).toBeCloseTo(lastOffset + gap, 5);
          lastOffset = slot.yOffset;
          expect(slot.eid).toBe(eid);
        }
      });
    }
  }

  for (let i = 0; i < 100; i++) {
    it(`independent keys do not cross-stack i=${i}`, () => {
      const eA = state.createEntity();
      const eB = state.createEntity();
      const keyA = `solo-a-${i}`;
      const keyB = `solo-b-${i}`;
      const a = claimStackSlot(state, keyA, eA, 2, 0.4);
      const b = claimStackSlot(state, keyB, eB, 2, 0.4);
      expect(a.yOffset).toBe(0);
      expect(b.yOffset).toBe(0);
    });
  }

  for (let i = 0; i < 40; i++) {
    it(`destroy releases slot before re-claim i=${i}`, () => {
      const e1 = state.createEntity();
      const e2 = state.createEntity();
      const key = `destroy-${i}`;
      claimStackSlot(state, key, e1, 8, 0.5);
      state.destroyEntity(e1);
      const slot = claimStackSlot(state, key, e2, 8, 0.5);
      expect(slot.yOffset).toBe(0);
    });
  }
});
