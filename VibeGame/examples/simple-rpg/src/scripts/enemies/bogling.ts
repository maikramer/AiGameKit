// bogling — swamp biome
import { createCreatureBehaviours } from '../creature';
import { CREATURE_DEFS } from '../../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.bogling);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
