// Relíquia solar — recompensa do obelisco do Deserto (120, 40). Pegar (F) dá
// ouro, que alimenta o ciclo de comércio com o mercador de Discordia.
// Módulo próprio por POI: ver nota em poi/watch-tome.ts.
import { createMysticObject } from '../../game/mystic.ts';
import { addGold } from '../../game/economy.ts';

const GOLD_REWARD = 80;

const relic = createMysticObject({
  modelUrl: '/assets/meshes/crystal_blue_lod1.glb',
  modelScale: 2.2,
  emissiveColor: 0xffc766,
  toastColor: '#ffe0a8',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Pegar a relíquia solar',
  message: `"O obelisco guardava isto desde antes da cidade ter nome. Vale bem uma bolsa."  (+${GOLD_REWARD} ouro)`,
  onRead: () => addGold(GOLD_REWARD),
});

export const start = relic.start;
export const update = relic.update;
