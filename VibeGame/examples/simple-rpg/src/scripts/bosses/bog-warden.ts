// Swamp elite boss — Bog Warden. Always-active elite at the far south of the
// swamp biome.
import { createCreatureBehaviours } from '../creature';
import { CREATURE_DEFS } from '../../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.bog_warden);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
