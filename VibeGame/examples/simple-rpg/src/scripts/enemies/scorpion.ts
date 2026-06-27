// scorpion — desert biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/scorpion_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 45,
  chaseSpeed: 1.5,
  wanderSpeed: 0.5,
  wanderRadius: 4,
  attackDamage: 14,
  lootGoldMin: 8,
  lootGoldMax: 18,
  enrageBelowFrac: 0.4,
  enemyType: 'scorpion',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
