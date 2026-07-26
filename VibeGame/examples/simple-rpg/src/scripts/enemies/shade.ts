// shade — dark forest biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  // shade_*.glb ships static (no skin/clips) — the XML visual uses the bogling
  // LOD triple at 1.4x, so the animator clips must come from that same master.
  modelUrl: '/assets/meshes/bogling_lod0.glb',
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
    hit: 'hit',
    attack: 'attack',
  },
  hp: 25,
  chaseSpeed: 2.2,
  wanderSpeed: 0.9,
  wanderRadius: 10,
  attackDamage: 15,
  detectRange: 18,
  leashRadius: 30,
  attackCooldown: 2.6,
  lootGoldMin: 10,
  lootGoldMax: 20,
  strafe: true,
  lowHpKiteFrac: 0.45,
  // Evasive glass-cannon: high damage but fragile, so it kites hard at low HP.
  // Long-ish windup telegraphs the strike; the threat is it relentless
  // re-engages from orbit rather than committing.
  lungeWindup: 0.32,
  lungeDuration: 0.26,
  lungeRecovery: 0.45,
  lungeStandoff: 1.0,
  enemyType: 'shade',
  behaviorProfile: { separate: true },
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
