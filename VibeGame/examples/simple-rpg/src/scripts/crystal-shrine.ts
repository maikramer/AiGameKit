// Resonant crystal (crystal_blue.glb). A glowing shrine; reading it (F) floods the
// player with experience toward the next level, then the crystal dims.
import { createMysticObject } from '../game/mystic.ts';
import { addXp } from 'aigamekit-vibegame';

const XP_REWARD = 50;

const shrine = createMysticObject({
  modelUrl: '/assets/meshes/props/crystal_blue_lod2.glb',
  emissiveColor: 0x3fd0ff,
  toastColor: '#9fe8ff',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Sintonizar com o cristal',
  message: `"O cristal canta, e o canto vira memória — você fica mais sábio."  (+${XP_REWARD} XP)`,
  onRead: (state, player) => addXp(state, player, XP_REWARD),
});

export const start = shrine.start;
export const update = shrine.update;
