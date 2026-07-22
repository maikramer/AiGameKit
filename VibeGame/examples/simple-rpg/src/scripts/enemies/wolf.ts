// wolf — dark forest biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/wolf_lod2.glb',
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
  chaseSpeed: 3.4,
  wanderSpeed: 1.3,
  wanderRadius: 14,
  attackDamage: 10,
  detectRange: 20,
  leashRadius: 34,
  attackCooldown: 1.8,
  lootGoldMin: 6,
  lootGoldMax: 14,
  strafe: true,
  lowHpKiteFrac: 0.3,
  enrageBelowFrac: 0.25,
  enemyType: 'wolf',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
