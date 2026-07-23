import { describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  deleteDestructiblePopupText,
  emitDestructibleDestroyed,
  getDestructiblePopupText,
  onDestructibleDestroyed,
  setDestructiblePopupText,
} from '../../../src/plugins/destructible/utils';

describe('destructible popup text sidecar', () => {
  for (let eid = 1; eid <= 30; eid++) {
    it(`set/get popup for entity ${eid}`, () => {
      const state = new State();
      const text = `loot-${eid}`;
      setDestructiblePopupText(state, eid, text);
      expect(getDestructiblePopupText(state, eid)).toBe(text);
      deleteDestructiblePopupText(state, eid);
      expect(getDestructiblePopupText(state, eid)).toBeUndefined();
    });
  }
});

describe('onDestructibleDestroyed callbacks', () => {
  for (let n = 0; n < 20; n++) {
    it(`invokes subscriber ${n} with world position`, () => {
      const state = new State();
      let hits = 0;
      const off = onDestructibleDestroyed(state, (entity, x, y, z) => {
        hits += 1;
        expect(entity).toBe(42);
        expect(x).toBeCloseTo(n, 5);
        expect(y).toBeCloseTo(n + 1, 5);
        expect(z).toBeCloseTo(n + 2, 5);
      });
      emitDestructibleDestroyed(state, 42, n, n + 1, n + 2);
      expect(hits).toBe(1);
      off();
      emitDestructibleDestroyed(state, 42, 0, 0, 0);
      expect(hits).toBe(1);
    });
  }
});

describe('popup text is isolated per State', () => {
  for (let i = 0; i < 15; i++) {
    it(`state pair ${i} does not leak popup strings`, () => {
      const a = new State();
      const b = new State();
      setDestructiblePopupText(a, 5, `a-${i}`);
      setDestructiblePopupText(b, 5, `b-${i}`);
      expect(getDestructiblePopupText(a, 5)).toBe(`a-${i}`);
      expect(getDestructiblePopupText(b, 5)).toBe(`b-${i}`);
    });
  }
});
