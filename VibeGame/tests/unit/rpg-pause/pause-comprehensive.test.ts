import { beforeEach, describe, expect, it } from 'bun:test';
import {
  PauseCoordinatorPlugin,
  PauseSystem,
  RpgCoreEventsPlugin,
  State,
  getActiveModal,
  getPauseState,
  isInputMovementSuppressed,
  isPaused,
  popModal,
  pushModal,
  setInputMovementSuppressed,
  setTimeScale,
  suppressInput,
} from 'aigamekit-vibegame';

describe('rpg-pause table-driven', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(RpgCoreEventsPlugin);
    state.registerPlugin(PauseCoordinatorPlugin);
    setInputMovementSuppressed(false);
  });

  const modalNames = [
    'pause-menu',
    'inventory',
    'shop',
    'dialog',
    'map',
    'settings',
    'craft',
    'trade',
    'quest-log',
    'confirm',
    'loading',
    'credits',
    'help',
    'character',
    'skills',
    'a',
    'b',
    'c',
    'nested-1',
    'nested-2',
  ];

  for (const name of modalNames) {
    it(`pushModal("${name}") pauses and sets active modal`, () => {
      pushModal(state, name);
      expect(isPaused(state)).toBe(true);
      expect(getActiveModal(state)).toBe(name);
      expect(state.time.timeScale).toBe(0);
    });
  }

  for (const name of modalNames) {
    it(`popModal after single push "${name}" resumes`, () => {
      pushModal(state, name);
      popModal(state);
      expect(isPaused(state)).toBe(false);
      expect(getActiveModal(state)).toBeUndefined();
    });
  }

  const timeScales = [
    0, 0.05, 0.1, 0.25, 0.33, 0.5, 0.66, 0.75, 0.9, 1, 1.25, 1.5, 2, 3, 0.001,
  ];

  for (const scale of timeScales) {
    it(`setTimeScale(${scale}) when unpaused writes state.time.timeScale`, () => {
      setTimeScale(state, scale);
      expect(state.time.timeScale).toBe(scale);
      expect(getPauseState(state).timeScale).toBe(scale);
    });
  }

  for (const scale of timeScales) {
    it(`setTimeScale(${scale}) restores after modal pop`, () => {
      setTimeScale(state, scale);
      pushModal(state, 'm');
      expect(state.time.timeScale).toBe(0);
      popModal(state);
      expect(state.time.timeScale).toBe(scale);
    });
  }

  for (let depth = 1; depth <= 12; depth++) {
    it(`stack depth ${depth} stays paused until all popped`, () => {
      for (let i = 0; i < depth; i++) pushModal(state, `layer-${i}`);
      expect(isPaused(state)).toBe(true);
      expect(getPauseState(state).modalStack).toHaveLength(depth);
      for (let i = 0; i < depth; i++) popModal(state);
      expect(isPaused(state)).toBe(false);
    });
  }

  const popTargets: Array<[string, string, string]> = [
    ['a', 'b', 'c'],
    ['x', 'y', 'z'],
    ['first', 'second', 'third'],
    ['menu', 'shop', 'dialog'],
    ['one', 'two', 'three'],
    ['alpha', 'beta', 'gamma'],
    ['p1', 'p2', 'p3'],
    ['inv', 'dlg', 'map'],
    ['m1', 'm2', 'm3'],
    ['top', 'mid', 'bot'],
  ];

  for (const [a, b, c] of popTargets) {
    it(`popModal("${b}") from stack [${a},${b},${c}]`, () => {
      pushModal(state, a);
      pushModal(state, b);
      pushModal(state, c);
      popModal(state, b);
      expect(getPauseState(state).modalStack).toEqual([a, c]);
      expect(getActiveModal(state)).toBe(c);
    });
  }

  for (const on of [true, false]) {
    for (let i = 0; i < 5; i++) {
      it(`suppressInput(${on}) when unpaused — case ${i}`, () => {
        suppressInput(state, on);
        expect(isInputMovementSuppressed()).toBe(on);
      });
    }
  }

  for (let i = 0; i < 10; i++) {
    it(`PauseSystem step ${i} enforces pause while modal open`, () => {
      pushModal(state, `sys-${i}`);
      state.time.timeScale = 1;
      setInputMovementSuppressed(false);
      state.registerSystem(PauseSystem);
      state.step();
      expect(state.time.timeScale).toBe(0);
      expect(isInputMovementSuppressed()).toBe(true);
    });
  }

  for (let i = 0; i < 8; i++) {
    it(`getPauseState initial snapshot ${i}`, () => {
      const ps = getPauseState(state);
      expect(ps.paused).toBe(false);
      expect(ps.modalStack).toEqual([]);
      expect(ps.timeScale).toBe(1);
      expect(ps.inputSuppressed).toBe(false);
    });
  }
});
