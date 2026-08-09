// Desert elite boss — Sand Worm / Verme das Areias. Always-active elite at the
// far east of the desert biome.
import { createCreatureBehaviours } from '../creature';
import { CREATURE_DEFS } from '../../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.sand_worm);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
