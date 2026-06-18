import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const { start, update, onDestroy } = createCreatureBehaviours({
  modelUrl: '/assets/meshes/goblin_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 40,
  chaseSpeed: 2.4,
  wanderSpeed: 1.0,
  wanderRadius: 12,
  attackDamage: 12,
  lootGoldMin: 8,
  lootGoldMax: 18,
  strafe: true,
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export { start, update, onDestroy };
