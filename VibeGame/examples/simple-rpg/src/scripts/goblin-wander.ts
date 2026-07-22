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
  chaseSpeed: 2.5,
  wanderSpeed: 1.1,
  wanderRadius: 12,
  attackDamage: 12,
  detectRange: 16,
  leashRadius: 28,
  attackCooldown: 2.2,
  lootGoldMin: 8,
  lootGoldMax: 18,
  strafe: true,
  lowHpKiteFrac: 0.35,
  enemyType: 'goblin',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export { start, update, onDestroy };
