// mosquito — swamp biome (Phase 1 placeholder uses goblin GLB)
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/mosquito_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
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
