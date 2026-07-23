// archer — plains biome. The game's first ranged attacker: holds a long
// stand-off, fires arrows on a cadence, and kites away when the hero closes.
// Reuses the humanoid bandit mesh as a placeholder until a dedicated archer
// asset is generated.
import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/bandit_lod2.glb',
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
  chaseSpeed: 2.2,
  wanderSpeed: 0.9,
  wanderRadius: 8,
  // The ranged layer suppresses the lunge; attackDamage scales the arrow
  // projectile damage defined by the `enemy-arrow` template in index.html.
  attackDamage: 8,
  detectRange: 19,
  leashRadius: 30,
  lootGoldMin: 12,
  lootGoldMax: 26,
  strafe: true,
  lowHpKiteFrac: 0.5,
  enemyType: 'bandit',
  rangedTemplate: 'enemy-arrow',
  rangedCooldown: 2.2,
  behaviorProfile: { separate: true, kite: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
