// wolf — dark forest biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/wolf_lod0.glb',
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
  chaseSpeed: 3.2,
  wanderSpeed: 1.2,
  wanderRadius: 14,
  attackDamage: 10,
  lootGoldMin: 6,
  lootGoldMax: 14,
  strafe: true,
  enemyType: 'wolf',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
