import type { State } from './ecs/state';

/**
 * World-readiness gate registry.
 *
 * Plugins register named "ready gates" (e.g. terrain decoded, spawns done,
 * GLTF assets loaded). A loading screen aggregates them to decide when the
 * world is fully loaded, and physics is held until then — so nothing simulates
 * or falls before the terrain colliders and assets are in place.
 *
 * Lives in core (not the loading plugin) so both physics and the plugins that
 * provide gates depend only on core — avoiding an import cycle.
 *
 * The registry is inert unless a loading screen enables *enforcement*
 * ({@link setLoadingEnforcement}). Without it, gates may be registered but the
 * physics hold never engages, so existing games/tests behave exactly as before.
 */
export type ReadyCheck = (state: State) => boolean;

interface LoadingGateState {
  gates: Map<string, ReadyCheck>;
  enforced: boolean;
  /** Latches true the first time the world is fully ready under enforcement. */
  loaded: boolean;
}

const stateToGate = new WeakMap<State, LoadingGateState>();

function getGateState(state: State): LoadingGateState {
  let g = stateToGate.get(state);
  if (!g) {
    g = { gates: new Map(), enforced: false, loaded: false };
    stateToGate.set(state, g);
  }
  return g;
}

/**
 * Register (or replace) a named readiness gate. Idempotent by name, so a gate
 * registered from a `setup` system can run every frame without duplicating.
 */
export function registerReadyGate(
  state: State,
  name: string,
  isReady: ReadyCheck
): void {
  getGateState(state).gates.set(name, isReady);
}

export function getReadyGates(state: State): string[] {
  return Array.from(getGateState(state).gates.keys());
}

/** True when every registered gate passes (vacuously true if there are none). */
export function isWorldReady(state: State): boolean {
  const g = getGateState(state);
  for (const [, check] of g.gates) {
    if (!check(state)) return false;
  }
  return true;
}

/** Progress snapshot for a loading UI: how many gates pass and which don't. */
export function getLoadingProgress(state: State): {
  ready: number;
  total: number;
  pending: string[];
} {
  const g = getGateState(state);
  const pending: string[] = [];
  let ready = 0;
  for (const [name, check] of g.gates) {
    if (check(state)) ready++;
    else pending.push(name);
  }
  return { ready, total: g.gates.size, pending };
}

/** Enable/disable physics-hold enforcement (a loading screen turns this on). */
export function setLoadingEnforcement(state: State, on: boolean): void {
  getGateState(state).enforced = on;
}

export function isLoadingEnforced(state: State): boolean {
  return getGateState(state).enforced;
}

/**
 * True once the world has been fully ready at least once under enforcement.
 * Latches permanently so transient un-readiness during gameplay (e.g. distant
 * terrain chunks rebuilding colliders) never re-engages the loading hold.
 */
export function isWorldLoadedLatched(state: State): boolean {
  const g = getGateState(state);
  if (g.loaded) return true;
  if (g.enforced && isWorldReady(state)) {
    g.loaded = true;
    return true;
  }
  return false;
}

/**
 * Whether physics (and gameplay) should be held this frame: enforcement is on
 * and the world has not yet finished its initial load.
 */
export function isPhysicsHeld(state: State): boolean {
  const g = getGateState(state);
  if (!g.enforced) return false;
  return !isWorldLoadedLatched(state);
}

/** Test helper: drop all gate state for a world. */
export function resetLoadingGate(state: State): void {
  stateToGate.delete(state);
}
