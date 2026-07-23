// bandit — plains/roads biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

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
  hp: 50,
  chaseSpeed: 2.6,
  wanderSpeed: 1.0,
  wanderRadius: 8,
  attackDamage: 13,
  detectRange: 17,
  leashRadius: 32,
  attackCooldown: 2.1,
  lootGoldMin: 15,
  lootGoldMax: 35,
  strafe: true,
  lowHpKiteFrac: 0.4,
  // Disciplined fighter: keeps a slightly longer reach, telegraphs clearly,
  // and circles between swings. Reads as a trained humanoid vs a feral wolf.
  lungeWindup: 0.28,
  lungeDuration: 0.3,
  lungeRecovery: 0.5,
  lungeStandoff: 1.1,
  enemyType: 'bandit',
  behaviorProfile: { separate: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
