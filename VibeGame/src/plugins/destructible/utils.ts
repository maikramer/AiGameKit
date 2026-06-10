import type { State } from '../../core';

const popupTextByState = new WeakMap<State, Map<number, string>>();

/** Popup string shown when the prop breaks (set via the `popup-text` adapter). */
export function setDestructiblePopupText(
  state: State,
  entity: number,
  text: string
): void {
  let m = popupTextByState.get(state);
  if (!m) {
    m = new Map();
    popupTextByState.set(state, m);
  }
  m.set(entity, text);
}

export function getDestructiblePopupText(
  state: State,
  entity: number
): string | undefined {
  return popupTextByState.get(state)?.get(entity);
}

export function deleteDestructiblePopupText(
  state: State,
  entity: number
): void {
  popupTextByState.get(state)?.delete(entity);
}

export type DestructibleDestroyedCallback = (
  entity: number,
  x: number,
  y: number,
  z: number
) => void;

const destroyedCallbacks = new WeakMap<
  State,
  Set<DestructibleDestroyedCallback>
>();

/**
 * Game hook fired when a destructible breaks (loot, inventory, SFX…).
 * Returns an unsubscribe function.
 */
export function onDestructibleDestroyed(
  state: State,
  callback: DestructibleDestroyedCallback
): () => void {
  let set = destroyedCallbacks.get(state);
  if (!set) {
    set = new Set();
    destroyedCallbacks.set(state, set);
  }
  set.add(callback);
  return () => set.delete(callback);
}

export function emitDestructibleDestroyed(
  state: State,
  entity: number,
  x: number,
  y: number,
  z: number
): void {
  const set = destroyedCallbacks.get(state);
  if (!set) return;
  for (const cb of set) {
    try {
      cb(entity, x, y, z);
    } catch (err) {
      console.error('[destructible] destroyed callback error:', err);
    }
  }
}
