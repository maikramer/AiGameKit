// bandit — plains/roads biome.
// Same melee timing/feel as goblin-wander (short jab, quick recovery). Identity
// (mesh, HP, loot, enemyType) stays bandit so desert quests keep counting kills.
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

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
  hp: 50,
  chaseSpeed: 2.7,
  wanderSpeed: 1.1,
  wanderRadius: 12,
  attackDamage: 13,
  detectRange: 16,
  leashRadius: 28,
  attackCooldown: 1.6,
  lootGoldMin: 15,
  lootGoldMax: 35,
  strafe: true,
  lowHpKiteFrac: 0.35,
  // Match goblin-wander lunge — longer windup/standoff made the CCT dash miss
  // after the first swing (see rpg-ai applyLungeMovement Rigidbody sync).
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
