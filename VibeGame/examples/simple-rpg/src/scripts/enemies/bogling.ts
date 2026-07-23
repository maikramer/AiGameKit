// bogling — swamp biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/bogling_lod2.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 30,
  chaseSpeed: 3.0,
  wanderSpeed: 1.0,
  wanderRadius: 6,
  attackDamage: 7,
  attackCooldown: 1.4,
  lootGoldMin: 4,
  lootGoldMax: 10,
  // Swarming pest: fragile, frantic, very short windup — throws itself at the
  // hero in numbers. Dies fast but interrupts casts/movement.
  lungeWindup: 0.12,
  lungeDuration: 0.2,
  lungeRecovery: 0.3,
  lungeStandoff: 0.7,
  enemyType: 'bogling',
  behaviorProfile: { separate: true, flock: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
