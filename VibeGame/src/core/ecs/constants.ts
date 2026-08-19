export const TIME_CONSTANTS = {
  FIXED_TIMESTEP: 1 / 50,
  DEFAULT_DELTA: 1 / 144,
  MAX_FRAME_DELTA: 0.25,
  MAX_FIXED_STEPS_PER_FRAME: 20,
} as const;

export const NULL_ENTITY = 4294967295;

const DEFAULT_MAX_ENTITIES = 100000;

/**
 * Override the entity budget before importing the engine:
 *
 * ```html
 * <script>globalThis.VIBEGAME_MAX_ENTITIES = 400000;</script>
 * <script type="module" src="/src/main.ts"></script>
 * ```
 *
 * Read once, at module init — the value sizes every component array, so it
 * cannot change afterwards.
 */
function resolveMaxEntities(): number {
  const requested = (globalThis as { VIBEGAME_MAX_ENTITIES?: unknown })
    .VIBEGAME_MAX_ENTITIES;
  if (typeof requested !== 'number') return DEFAULT_MAX_ENTITIES;
  if (!Number.isFinite(requested) || requested < 1) return DEFAULT_MAX_ENTITIES;
  return Math.ceil(requested);
}

/**
 * Entity-id ceiling, and the length of every component field array.
 *
 * A dressed open world spends one entity per placed prop — simple-rpg reaches
 * **93 194**, 93% of this cap — and past the cap component writes land outside
 * the typed array and are silently dropped (`State.createEntity` warns at 90%
 * and throws at the cap).
 *
 * **Raising this is not a linear trade.** Measured on simple-rpg (same scene,
 * same 93k entities, heap right after boot): 100k → 1781 MB, 150k → 3669 MB.
 * A 50% bigger cap cost more than double the heap and left the page near
 * Chrome's ~4 GB limit, so the default stays where the content fits. A game
 * that genuinely needs more can opt in via `VIBEGAME_MAX_ENTITIES` — but the
 * cheaper fix is almost always fewer entities (instanced foliage that spends
 * one entity per blade is the usual reason for a 90k count).
 *
 * Component storage being lazy (`component-storage.ts`) is what keeps the cap
 * from being charged for all ~104 components at import time; it does not make
 * the cap itself cheap for a game that touches most of them.
 */
export const MAX_ENTITIES = resolveMaxEntities();
