// shade — dark forest biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/shade_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Run',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 25,
  chaseSpeed: 2.0,
  wanderSpeed: 0.8,
  wanderRadius: 10,
  attackDamage: 14,
  lootGoldMin: 10,
  lootGoldMax: 20,
  lowHpKiteFrac: 0.3,
  enemyType: 'shade',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
