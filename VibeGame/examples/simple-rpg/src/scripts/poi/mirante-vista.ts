// O Cairn do Mirante — no alto da borda sul da caldeira (-605,-725).
// Contemplar (F) rende experiência: ver o mundo inteiro também é anotar.
import { createMysticObject } from '../../game/mystic.ts';
import { addXp } from 'vibegame';

const XP_REWARD = 80;

const vista = createMysticObject({
  modelUrl: '/assets/meshes/terrain/stone_cairn_lod1.glb',
  modelScale: 1.15,
  emissiveColor: 0xffd24a,
  toastColor: '#ffe9a0',
  readRangeSq: 5.2 * 5.2,
  promptLabel: 'Contemplar o mundo',
  message: `"Do alto da borda, a caldeira inteira cabe no olhar — e o que é anotado, permanece."  (+${XP_REWARD} XP)`,
  onRead: (state, player) => addXp(state, player, XP_REWARD),
});

export const start = vista.start;
export const update = vista.update;
