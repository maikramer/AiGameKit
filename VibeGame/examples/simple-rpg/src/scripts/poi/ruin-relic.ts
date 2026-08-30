// O Coração de Orm — relíquia no centro da ruína ancestral (1700,-810).
// Tocar (F) rende ouro e experiência; o cristal apaga depois de lido.
import { createMysticObject } from '../../game/mystic.ts';
import { addXp } from 'aigamekit-vibegame';
import { addGold } from '../../game/economy.ts';

const GOLD_REWARD = 150;
const XP_REWARD = 60;

const relic = createMysticObject({
  modelUrl: '/assets/meshes/props/crystal_blue_lod1.glb',
  modelScale: 1.2,
  emissiveColor: 0x9fd8ff,
  toastColor: '#bfe4ff',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Tocar o Coração de Orm',
  message: `"Os saqueadores cavaram por décadas. O coração esperava um Anotador."  (+${GOLD_REWARD} ouro, +${XP_REWARD} XP)`,
  onRead: (state, player) => {
    addGold(GOLD_REWARD);
    addXp(state, player, XP_REWARD);
  },
});

export const start = relic.start;
export const update = relic.update;
