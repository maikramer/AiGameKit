// shade — dark forest biome
import { createCreatureBehaviours } from '../creature';
import { CREATURE_DEFS } from '../../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.shade);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
