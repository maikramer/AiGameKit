// scorpion — desert biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/scorpion_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 55,
  chaseSpeed: 1.6,
  wanderSpeed: 0.5,
  wanderRadius: 4,
  attackDamage: 18,
  attackCooldown: 2.8,
  lootGoldMin: 8,
  lootGoldMax: 18,
  enrageBelowFrac: 0.4,
  // Heavy bruiser: long, readable telegraph before the sting — a player who
  // watches can sidestep. When it does connect, it hurts. Enrage shortens the
  // windup window dramatically (becomes dangerous at low HP).
  lungeWindup: 0.5,
  lungeDuration: 0.28,
  lungeRecovery: 0.6,
  lungeStandoff: 1.0,
  enrageSpeedMult: 1.5,
  enrageCooldownMult: 0.45,
  enemyType: 'scorpion',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
