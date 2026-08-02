// Plains skirmisher — same mesh as bandit, same melee feel as goblin-wander.
// Was a ranged placeholder (`enemy-arrow`); identical bandit GLB + attack clip
// in ATTACK mode looked like melee swings that never dealt close damage
// (lunge suppressed by rangedTemplate). Until a dedicated archer asset exists,
// fight like the melee bandit / goblin.
import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/characters/bandit_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 38,
  chaseSpeed: 2.7,
  wanderSpeed: 1.1,
  wanderRadius: 12,
  attackDamage: 9,
  detectRange: 16,
  leashRadius: 28,
  attackCooldown: 1.6,
  lootGoldMin: 12,
  lootGoldMax: 26,
  strafe: true,
  lowHpKiteFrac: 0.35,
  lungeWindup: 0.18,
  lungeDuration: 0.22,
  lungeRecovery: 0.35,
  lungeStandoff: 0.8,
  enemyType: 'bandit',
  behaviorProfile: { separate: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
