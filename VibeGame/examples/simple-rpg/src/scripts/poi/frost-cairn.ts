// Cairn gelado — recompensa do lago gelado dos Picos (-92, -34), no desvio a
// caminho da arena do ogro. Dá experiência antes do chefe final.
// Módulo próprio por POI: ver nota em poi/watch-tome.ts.
import { createMysticObject } from '../../game/mystic.ts';
import { addXp } from 'aigamekit-vibegame';

const XP_REWARD = 60;

const cairn = createMysticObject({
  modelUrl: '/assets/meshes/props/crystal_blue_lod1.glb',
  modelScale: 2.4,
  emissiveColor: 0x8fd8ff,
  toastColor: '#cbeeff',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Ler o mojão gelado',
  message: `"As pedras empilhadas apontam para o oeste. Quem as colocou aqui sabia o que espera na arena."  (+${XP_REWARD} XP)`,
  onRead: (state, player) => addXp(state, player, XP_REWARD),
});

export const start = cairn.start;
export const update = cairn.update;
