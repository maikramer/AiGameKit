// Single binding point between the gameplay scripts and the engine ECS. The
// economy/inventory/skill/pause adapters below forward to the engine plugins
// (RpgVault / Progression / PauseCoordinator) so there is ONE source of truth —
// the hero entity's components — instead of legacy module-global counters.

import type { State } from 'vibegame';

let boundState: State | null = null;
let cachedHero = 0;

/** Called once from bootstrap after the runtime is built. */
export function bindEngine(state: State): void {
  boundState = state;
  cachedHero = 0;
}

export function engineState(): State | null {
  return boundState;
}

/** Lazily resolve (and cache) the hero entity id. */
export function heroEid(): number {
  if (!boundState) return 0;
  if (cachedHero) return cachedHero;
  cachedHero = boundState.getEntityByName('hero') ?? 0;
  return cachedHero;
}
