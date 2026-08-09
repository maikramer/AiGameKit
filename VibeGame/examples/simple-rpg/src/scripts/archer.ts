// Plains skirmisher — same mesh as bandit, same melee feel as goblin-wander.
// Until a dedicated archer asset exists, fight like the melee bandit.
import { createCreatureBehaviours } from './creature';
import { CREATURE_DEFS } from '../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.archer);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
