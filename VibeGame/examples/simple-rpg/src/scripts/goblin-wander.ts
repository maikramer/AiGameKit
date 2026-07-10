import { createCreatureBehaviours } from './creature';
import { addGold } from '../game/economy';

const { start, update, onDestroy } = createCreatureBehaviours({
  modelUrl: '/assets/meshes/goblin_rigged_animated.glb',
  modelScale: 0.65,
  clips: {
    idle: 'idle',
    walk: 'walk',
    run: 'run',
    lunge: 'jump',
    death: 'death',
  },
  hp: 40,
  chaseSpeed: 2.4,
  wanderSpeed: 1.0,
  wanderRadius: 12,
  attackDamage: 12,
  lootGoldMin: 8,
  lootGoldMax: 18,
  strafe: true,
  enemyType: 'goblin',
  onDeathLoot: (state, gold, x, y, z) => addGold(gold, x, y, z),
});

export { start, update, onDestroy };
