// Boss ogre — same engine MeleeAi FSM as the creatures (one AI brain), with
// boss extras layered on by the shared presentation: dormant until every normal
// enemy is dead (gate), an intro roar on reveal, relentless pursuit (huge detect
// + leash), strafing, and an enrage phase at low HP.
import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';
import { everSpawned, aliveInBiome } from './enemy-registry';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/boss_ogre_rigged_animated.glb',
  clips: {
    idle: 'Animator3D_BreatheIdle',
    walk: 'Animator3D_Walk',
    run: 'Animator3D_Walk',
    lunge: 'Animator3D_Attack',
    death: 'Animator3D_Fall',
    roar: 'Animator3D_Roar',
  },
  hp: 300,
  chaseSpeed: 3.0,
  wanderSpeed: 0, // stays put until it spots the hero, then hunts
  wanderRadius: 1,
  attackDamage: 25,
  attackRange: 2.2,
  attackCooldown: 1.6,
  detectRange: 120, // relentless — always sees the hero once awake
  leashRadius: 1000, // never leashes home
  strafe: true,
  enrageBelowFrac: 0.3,
  runTimeScale: 1.5,
  lootGoldMin: 100,
  lootGoldMax: 150,
  defeatedText: 'BOSS DEFEATED!',
  roarSound: 'boss-roar',
  // Gate: appear only after every enemy in the frozen-peaks biome is dead.
  gateUntil: () => everSpawned() && aliveInBiome('frozen-peaks') === 0,
  enemyType: 'boss_ogre',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
