// bandit — plains/roads biome (Phase 1 placeholder uses goblin GLB)
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/bandit_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 50,
  chaseSpeed: 2.4,
  wanderSpeed: 1.0,
  wanderRadius: 8,
  attackDamage: 12,
  lootGoldMin: 15,
  lootGoldMax: 35,
  strafe: true,
  enemyType: 'bandit',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
