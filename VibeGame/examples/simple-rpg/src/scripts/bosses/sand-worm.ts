// Desert elite boss — Sand Worm / Verme das Areias. Always-active elite at the far east
// of the desert biome.
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/characters/sand_worm_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'walk',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 190,
  chaseSpeed: 3.4,
  wanderSpeed: 0.8,
  wanderRadius: 8,
  attackDamage: 22,
  attackRange: 2.2,
  attackCooldown: 2.0,
  detectRange: 26,
  leashRadius: 50,
  strafe: false,
  enrageBelowFrac: 0.35,
  runTimeScale: 1.5,
  lootGoldMin: 90,
  lootGoldMax: 130,
  defeatedText: 'SARRA FOI DESFEITA!',
  enemyType: 'boss_sandworm',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
