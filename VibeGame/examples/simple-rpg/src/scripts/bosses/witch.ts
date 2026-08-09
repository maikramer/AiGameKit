// Forest elite boss — Bruxa da Floresta. Always-active elite at the far north
// of the dark forest biome (no global gate — it guards its lair until
// approached).
import { createCreatureBehaviours } from '../creature';
import { CREATURE_DEFS } from '../../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.witch);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
