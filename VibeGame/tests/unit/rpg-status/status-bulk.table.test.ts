import { beforeAll, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import {
  RpgCoreEventsPlugin,
  State,
  STATUS_APPLIED,
  STATUS_CANCELLED,
  STATUS_EXPIRED,
  StatusEffectComponent,
  StatusEffectsPlugin,
  applyStatus,
  cancelStatus,
  getActiveStatuses,
  getStatusModifiers,
  getDataRegistry,
} from 'aigamekit-vibegame';
import type { StatusEffectDef } from 'aigamekit-vibegame';

beforeAll(() => {
  const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
  globalThis.DOMParser = dom.window.DOMParser;
});

const STATUS_KIND = 'status';

function def(
  id: string,
  duration: number,
  mods: StatusEffectDef['modifiers'] = []
): StatusEffectDef {
  return { id, name: id, duration, modifiers: mods };
}

function stateWithRegistry(extra: StatusEffectDef[]): State {
  const state = new State();
  state.registerPlugin(RpgCoreEventsPlugin);
  state.registerPlugin(StatusEffectsPlugin);
  const reg = getDataRegistry(state);
  for (const d of extra) reg.register(STATUS_KIND, d.id, d);
  return state;
}

describe('applyStatus stack modes duration math', () => {
  for (const mode of ['replace', 'stack', 'max'] as const) {
    it(`${mode} re-apply`, () => {
      const state = stateWithRegistry([def('s', 10)]);
      const eid = state.createEntity();
      applyStatus(state, eid, 's', { stackMode: mode });
      applyStatus(state, eid, 's', { stackMode: mode });
      const after = getActiveStatuses(state, eid)[0]!;
      if (mode === 'replace') expect(after.remainingTime).toBe(10);
      else if (mode === 'stack') expect(after.remainingTime).toBe(20);
      else expect(after.remainingTime).toBe(10);
    });
  }

  for (const mode of ['replace', 'stack', 'max'] as const) {
    for (const n of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]) {
      it(`${mode} keeps single slot after ${n} applies`, () => {
        const state = stateWithRegistry([def('s', 5 + n)]);
        const eid = state.createEntity();
        for (let k = 0; k < n; k += 1)
          applyStatus(state, eid, 's', { stackMode: mode });
        expect(getActiveStatuses(state, eid).length).toBe(1);
      });
    }
  }
});

describe('cancelStatus and modifiers cleared', () => {
  for (let i = 0; i < 25; i += 1) {
    it(`cancel buff-${i}`, () => {
      const id = `buff-${i}`;
      const state = stateWithRegistry([
        def(id, 5, [{ stat: 'armor', magnitude: i + 1, stackMode: 'stack' }]),
      ]);
      const eid = state.createEntity();
      applyStatus(state, eid, id);
      expect(getStatusModifiers(state, eid).length).toBe(1);
      cancelStatus(state, eid, id);
      expect(getActiveStatuses(state, eid).length).toBe(0);
      expect(getStatusModifiers(state, eid).length).toBe(0);
    });
  }
});

describe('StatusEffectComponent count tracks active list', () => {
  for (let n = 1; n <= 8; n += 1) {
    it(`${n} statuses`, () => {
      const defs = Array.from({ length: n }, (_, k) =>
        def(`d-${k}`, 4, [
          { stat: 'speed', magnitude: 1, stackMode: 'replace' },
        ])
      );
      const state = stateWithRegistry(defs);
      const eid = state.createEntity();
      for (const d of defs) applyStatus(state, eid, d.id);
      expect(StatusEffectComponent.count[eid]).toBe(n);
      expect(getActiveStatuses(state, eid).length).toBe(n);
    });
  }
});

describe('tick expiry reduces count', () => {
  for (let duration of [0.5, 1, 2, 3]) {
    it(`duration ${duration}`, () => {
      const state = stateWithRegistry([def('short', duration)]);
      const eid = state.createEntity();
      applyStatus(state, eid, 'short');
      expect(StatusEffectComponent.count[eid]).toBe(1);
      for (let i = 0; i < duration + 2; i += 1) state.step(1);
      expect(StatusEffectComponent.count[eid]).toBe(0);
    });
  }
});

describe('unknown status id is no-op', () => {
  for (let i = 0; i < 20; i += 1) {
    it(`missing-${i}`, () => {
      const state = stateWithRegistry([]);
      const eid = state.createEntity();
      applyStatus(state, eid, `missing-${i}`);
      expect(StatusEffectComponent.count[eid]).toBe(0);
    });
  }
});

describe('STATUS event names registered', () => {
  for (const ev of [STATUS_APPLIED, STATUS_CANCELLED, STATUS_EXPIRED]) {
    it(ev, () => {
      expect(typeof ev).toBe('string');
      expect(ev.startsWith('status:')).toBe(true);
    });
  }
});
