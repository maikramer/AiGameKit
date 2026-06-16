import type { State, System } from '../../core';
import { emitEvent } from '../rpg-core/events';
import { setInputMovementSuppressed } from '../input';

export const PAUSE_PUSHED = 'pause:pushed';
export const PAUSE_POPPED = 'pause:popped';
export const PAUSE_CHANGED = 'pause:changed';

export interface PauseState {
  paused: boolean;
  modalStack: string[];
  inputSuppressed: boolean;
  timeScale: number;
}

function createPauseState(): PauseState {
  return {
    paused: false,
    modalStack: [],
    inputSuppressed: false,
    timeScale: 1,
  };
}

const states = new WeakMap<State, PauseState>();

export function getPauseState(state: State): PauseState {
  let ps = states.get(state);
  if (!ps) {
    ps = createPauseState();
    states.set(state, ps);
  }
  return ps;
}

function applyEffects(state: State): void {
  sync(state, true);
}

function sync(state: State, emitOnChange: boolean): void {
  const ps = getPauseState(state);
  const shouldPause = ps.modalStack.length > 0;

  if (emitOnChange && shouldPause !== ps.paused) {
    ps.paused = shouldPause;
    emitEvent(state, PAUSE_CHANGED, undefined);
  } else {
    ps.paused = shouldPause;
  }

  state.time.timeScale = shouldPause ? 0 : ps.timeScale;
  setInputMovementSuppressed(shouldPause ? true : ps.inputSuppressed);
}

export function pushModal(state: State, name: string): void {
  const ps = getPauseState(state);
  ps.modalStack.push(name);
  emitEvent(state, PAUSE_PUSHED, { modal: name, stack: [...ps.modalStack] });
  applyEffects(state);
}

export function popModal(state: State, name?: string): void {
  const ps = getPauseState(state);
  const stack = ps.modalStack;
  if (stack.length === 0) return;

  let popped: string | undefined;
  if (name === undefined) {
    popped = stack.pop();
  } else {
    const idx = stack.lastIndexOf(name);
    if (idx === -1) return;
    popped = stack.splice(idx, 1)[0];
  }

  if (popped === undefined) return;
  emitEvent(state, PAUSE_POPPED, { modal: popped, stack: [...stack] });
  applyEffects(state);
}

export function isPaused(state: State): boolean {
  return getPauseState(state).modalStack.length > 0;
}

export function getActiveModal(state: State): string | undefined {
  const stack = getPauseState(state).modalStack;
  return stack.length > 0 ? stack[stack.length - 1] : undefined;
}

export function setTimeScale(state: State, scale: number): void {
  const ps = getPauseState(state);
  ps.timeScale = scale;
  if (ps.modalStack.length === 0) {
    state.time.timeScale = scale;
  }
}

export function suppressInput(state: State, on: boolean): void {
  getPauseState(state).inputSuppressed = on;
  if (getPauseState(state).modalStack.length === 0) {
    setInputMovementSuppressed(on);
  }
}

export const PauseSystem: System = {
  group: 'late',
  update(state: State): void {
    sync(state, true);
  },
};
