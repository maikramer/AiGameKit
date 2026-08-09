import { defineQuery, PlayerController, Transform } from 'vibegame';
import type { State } from 'vibegame';

const playerQuery = defineQuery([PlayerController]);
let cachedPlayer = 0;

/**
 * First player entity — cached, re-validated on every call (a recycled eid
 * can't return a stale player). Replaces the per-module copies in the chest,
 * mystic, merchant and portal scripts.
 */
export function findPlayer(state: State): number {
  if (cachedPlayer && Transform.posX[cachedPlayer] !== undefined) {
    return cachedPlayer;
  }
  cachedPlayer = playerQuery(state.world)[0] ?? 0;
  return cachedPlayer;
}
