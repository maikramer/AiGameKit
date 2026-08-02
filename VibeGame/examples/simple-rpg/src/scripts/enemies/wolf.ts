// wolf — dark forest biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/characters/wolf_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 35,
  chaseSpeed: 3.6,
  wanderSpeed: 1.3,
  wanderRadius: 14,
  attackDamage: 12,
  detectRange: 20,
  leashRadius: 34,
  attackCooldown: 2.4,
  lootGoldMin: 6,
  lootGoldMax: 14,
  strafe: true,
  lowHpKiteFrac: 0.3,
  enrageBelowFrac: 0.25,
  // Hit-and-run: almost no telegraph, a fast long dash in, then a long recovery
  // where it backs off (lowHpKite + strafe). It never stands in melee range.
  lungeWindup: 0.1,
  lungeDuration: 0.35,
  lungeRecovery: 0.7,
  lungeStandoff: 1.2,
  enemyType: 'wolf',
  behaviorProfile: { separate: true, flock: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
