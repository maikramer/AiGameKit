// Forest elite boss — Bruxa da Floresta. Same engine MeleeAi FSM as the
// creatures, tuned as an always-active elite at the far north of the dark
// forest biome (no global gate — it guards its lair until approached).
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/witch_boss_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Walk',
    lunge: 'Animator3D_Jump',
    death: 'Animator3D_Fall',
  },
  hp: 160,
  chaseSpeed: 3.0,
  wanderSpeed: 0.6,
  wanderRadius: 6,
  attackDamage: 18,
  attackRange: 1.8,
  attackCooldown: 1.8,
  detectRange: 24,
  leashRadius: 45,
  strafe: true,
  enrageBelowFrac: 0.3,
  runTimeScale: 1.5,
  lootGoldMin: 70,
  lootGoldMax: 110,
  defeatedText: 'BRUXA DERROTADA!',
  enemyType: 'boss_witch',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
