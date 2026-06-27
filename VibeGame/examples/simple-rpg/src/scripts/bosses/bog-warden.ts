// Swamp elite boss — Bog Warden. Always-active elite at the far south of the
// swamp biome.
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/bog_warden_boss_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Walk',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 175,
  chaseSpeed: 2.8,
  wanderSpeed: 0.6,
  wanderRadius: 6,
  attackDamage: 20,
  attackRange: 2.0,
  attackCooldown: 1.9,
  detectRange: 24,
  leashRadius: 45,
  strafe: true,
  enrageBelowFrac: 0.3,
  lootGoldMin: 80,
  lootGoldMax: 120,
  defeatedText: 'BOG WARDEN DERROTADO!',
  enemyType: 'boss_bogwarden',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
