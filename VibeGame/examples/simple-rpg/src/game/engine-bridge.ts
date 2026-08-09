// Single binding point between the gameplay scripts and the engine ECS. The
// economy/inventory/skill/pause adapters below forward to the engine plugins
// (RpgVault / Progression / PauseCoordinator) so there is ONE source of truth —
// the player entity's components — instead of legacy module-global counters.

import type { State } from 'vibegame';

let boundState: State | null = null;
let cachedPlayer = 0;

/** Called once from bootstrap after the runtime is built. */
export function bindEngine(state: State): void {
  boundState = state;
  cachedPlayer = 0;
}

export function engineState(): State | null {
  return boundState;
}

/** Lazily resolve (and cache) the player entity id. */
export function playerEid(): number {
  if (!boundState) return 0;
  if (cachedPlayer) return cachedPlayer;
  cachedPlayer = boundState.getEntityByName('player') ?? 0;
  return cachedPlayer;
}
