// bandit — plains/roads biome
import { createCreatureBehaviours } from '../creature';
import { addGold } from '../../game/economy';

const behaviours = createCreatureBehaviours({
  modelUrl: '/assets/meshes/bandit_lod0.glb',
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
  chaseSpeed: 2.4,
  wanderSpeed: 1.0,
  wanderRadius: 8,
  attackDamage: 12,
  lootGoldMin: 15,
  lootGoldMax: 35,
  strafe: true,
  enemyType: 'bandit',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export const start = behaviours.start;
export const update = behaviours.update;
export const onDestroy = behaviours.onDestroy;
