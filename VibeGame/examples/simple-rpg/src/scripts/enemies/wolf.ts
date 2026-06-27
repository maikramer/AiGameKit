// wolf — dark forest biome (Phase 1 placeholder uses goblin GLB)
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/wolf_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 35,
  chaseSpeed: 3.2,
  wanderSpeed: 1.2,
  wanderRadius: 14,
  attackDamage: 10,
  lootGoldMin: 6,
  lootGoldMax: 14,
  strafe: true,
  enemyType: 'wolf',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
