import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/slime_rigged_animated.glb',
  modelScale: 0.45,
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 50,
  chaseSpeed: 1.8,
  wanderSpeed: 0.4,
  wanderRadius: 8,
  attackDamage: 18,
  lootGoldMin: 15,
  lootGoldMax: 30,
  strafe: true,
  lowHpKiteFrac: 0.35,
  enemyType: 'slime',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
