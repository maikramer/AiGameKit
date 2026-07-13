// bogling — swamp biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/bogling_rigged_animated.glb',
  modelScale: 0.55,
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 30,
  chaseSpeed: 2.8,
  wanderSpeed: 1.0,
  wanderRadius: 6,
  attackDamage: 8,
  lootGoldMin: 4,
  lootGoldMax: 10,
  enemyType: 'bogling',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
