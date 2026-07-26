// Tomo do vigia — recompensa do posto avançado arruinado da Floresta Sombria
// (-46, 88). Ler (F) dá experiência e apaga o brilho.
//
// Cada POI místico precisa do seu próprio módulo: createMysticObject fecha o
// estado (lido/por ler, grupo Three.js) em variáveis de módulo, por isso dois
// POIs compartilhando o mesmo arquivo compartilhariam o "já lido".
import { createMysticObject } from '../../game/mystic.ts';
import { addXp } from 'vibegame';

const XP_REWARD = 40;

const tome = createMysticObject({
  modelUrl: '/assets/meshes/treasure_chest_lod1.glb',
  modelScale: 0.8,
  emissiveColor: 0xffd24a,
  toastColor: '#ffe9a0',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Ler o tomo do vigia',
  message: `"O diário do último vigia termina no meio de uma frase — mas o que está ali te ensina a ler a floresta."  (+${XP_REWARD} XP)`,
  onRead: (state, player) => addXp(state, player, XP_REWARD),
});

export const start = tome.start;
export const update = tome.update;
