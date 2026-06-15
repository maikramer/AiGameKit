import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/slime_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 50,
  chaseSpeed: 1.8,
  wanderSpeed: 0.4,
  wanderRadius: 8,
  attackDamage: 18,
  lootGoldMin: 15,
  lootGoldMax: 30,
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
