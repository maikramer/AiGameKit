import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/characters/slime_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 60,
  chaseSpeed: 1.6,
  wanderSpeed: 0.4,
  wanderRadius: 8,
  attackDamage: 16,
  attackCooldown: 1.8,
  lootGoldMin: 15,
  lootGoldMax: 30,
  // Tanky blob: no strafe, no kite — it presses into melee and stays there,
  // oozing onto the hero. Slow but durable; short lunge, short recovery so it
  // keeps contact rather than darting away.
  lungeWindup: 0.3,
  lungeDuration: 0.2,
  lungeRecovery: 0.3,
  lungeStandoff: 0.6,
  enemyType: 'slime',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
