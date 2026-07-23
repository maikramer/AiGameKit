// Aggro chain: when one enemy takes damage from the hero, nearby awake allies
// with line of sight to the hero are alerted and acquire the hero as target.
// This is what makes a pack feel like a pack — hit one goblin and the rest
// turn on you — instead of every creature aggroing in isolation.
//
// Wired from main.ts once after the game state is built:
//   setupAggroChain(state);
//
// The chain is intentionally conservative:
//   - Only the hero counts as an aggressor (this game has no friendly fire).
//   - Only awake (non-sleeping) allies within `CALL_RADIUS` are alerted, and
//     only if they can SEE the hero (LOS) — no calling through walls.
//   - The alerted ally gets the hero locked as its explicit target, overriding
//     its lazy per-frame acquisition so it commits to the fight immediately.

import {
  onEvent,
  COMBAT_DAMAGED,
  Transform,
  AiStateComponent,
  getMeleeAiConfig,
  hasLineOfSight,
} from 'vibegame';
import type { State } from 'vibegame';
import { defineQuery, PlayerController, Health } from 'vibegame';
import { aliveEnemyCount } from './enemy-registry';

const playerQuery = defineQuery([PlayerController]);
const enemyQuery = defineQuery([AiStateComponent, Health, Transform]);

/** Distance (m) within which an ally hears a comrade under attack. */
const CALL_RADIUS = 16;
const CALL_RADIUS_SQ = CALL_RADIUS * CALL_RADIUS;

/**
 * Register the aggro-chain listener on `state`. Returns the subscription id
 * (for diagnostics / future teardown). Idempotent: safe to call multiple times
 * (each call adds a fresh subscription).
 */
export function setupAggroChain(state: State): number {
  return onEvent(state, COMBAT_DAMAGED, (payload) => {
    const p = payload as { target?: unknown } | undefined;
    const target = typeof p?.target === 'number' ? p.target : 0;
    if (target <= 0) return;
    // Only react when an AI entity (has AiStateComponent) took damage.
    const comp = AiStateComponent;
    if (comp.mode[target] === undefined) return;

    const hero = playerQuery(state.world)[0] ?? 0;
    if (hero <= 0 || Health.current[hero] <= 0) return;

    const hx = Transform.posX[hero];
    const hz = Transform.posZ[hero];
    const tx = Transform.posX[target];
    const tz = Transform.posZ[target];

    // Wake / re-target every awake ally near the victim that can see the hero.
    for (const ally of enemyQuery(state.world)) {
      if (ally === target) continue;
      if (Health.current[ally] <= 0) continue;
      const dx = Transform.posX[ally] - tx;
      const dz = Transform.posZ[ally] - tz;
      if (dx * dx + dz * dz > CALL_RADIUS_SQ) continue;
      // LOS from the ally to the hero (not to the victim): we want allies that
      // can actually join the fight, not ones that hear a scream behind a wall.
      if (!hasLineOfSight(state, Transform.posX[ally], Transform.posZ[ally], hx, hz)) {
        continue;
      }
      const cfg = getMeleeAiConfig(state, ally);
      if (cfg) cfg.targetEid = hero;
      comp.target[ally] = hero;
      // Nudge a sleeping/IDLE ally out of idle so it commits this frame.
      if (comp.mode[ally] === 0 /* AI_MODE_IDLE */) {
        comp.mode[ally] = 2 /* AI_MODE_CHASE */;
      }
    }
  });
}

/** Helper for tests / diagnostics: how many enemies are currently alive. */
export function aggroChainPopulation(): number {
  return aliveEnemyCount();
}
