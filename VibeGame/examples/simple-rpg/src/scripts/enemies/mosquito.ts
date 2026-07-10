// mosquito — swamp biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/mosquito_rigged_animated.glb',
  modelScale: 0.3,
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
  },
  hp: 12,
  chaseSpeed: 2.0,
  wanderSpeed: 0.7,
  wanderRadius: 4,
  attackDamage: 4,
  lootGoldMin: 2,
  lootGoldMax: 6,
  enemyType: 'mosquito',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
