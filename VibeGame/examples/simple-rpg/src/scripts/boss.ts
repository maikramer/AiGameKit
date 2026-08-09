// Boss ogre — same engine MeleeAi FSM as the creatures (one AI brain), with
// boss extras layered on by the shared presentation (gate, roar, enrage).
import { createCreatureBehaviours } from './creature';
import { CREATURE_DEFS } from '../data/creature-defs';

const behaviours = createCreatureBehaviours(CREATURE_DEFS.boss_ogre);

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
