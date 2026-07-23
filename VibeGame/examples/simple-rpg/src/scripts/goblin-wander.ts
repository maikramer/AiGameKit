import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const { start, update, onDestroy } = createCreatureBehaviours({
  modelUrl: '/assets/meshes/goblin_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 40,
  chaseSpeed: 2.7,
  wanderSpeed: 1.1,
  wanderRadius: 12,
  attackDamage: 9,
  detectRange: 16,
  leashRadius: 28,
  attackCooldown: 1.6,
  lootGoldMin: 8,
  lootGoldMax: 18,
  strafe: true,
  lowHpKiteFrac: 0.35,
  // Agile skirmisher: short telegraph, quick jab, fast recovery — hits often
  // but soft. Packs weave around the hero rather than body-blocking.
  lungeWindup: 0.18,
  lungeDuration: 0.22,
  lungeRecovery: 0.35,
  lungeStandoff: 0.8,
  enemyType: 'goblin',
  behaviorProfile: { separate: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export { start, update, onDestroy };
