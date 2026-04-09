import type { State } from '../../core';

const stateToStrings = new WeakMap<State, string[]>();

export function getStringPool(state: State): string[] {
  let pool = stateToStrings.get(state);
  if (!pool) {
    pool = [''];
    stateToStrings.set(state, pool);
  }
  return pool;
}

export function internString(state: State, s: string): number {
  const pool = getStringPool(state);
  const idx = pool.indexOf(s);
  if (idx >= 0) return idx;
  pool.push(s);
  return pool.length - 1;
}

export function getStringAt(state: State, index: number): string {
  const pool = getStringPool(state);
  return pool[index] ?? '';
}
