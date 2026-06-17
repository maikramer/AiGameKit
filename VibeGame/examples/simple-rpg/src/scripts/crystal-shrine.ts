// Resonant crystal (crystal_blue.glb). A glowing shrine; reading it (F) floods the
// hero with experience toward the next level, then the crystal dims.
import { createMysticObject } from '../game/mystic.ts';
import { addXp } from 'vibegame';

const XP_REWARD = 50;

const shrine = createMysticObject({
  modelUrl: '/assets/meshes/crystal_blue.glb',
  emissiveColor: 0x3fd0ff,
  toastColor: '#9fe8ff',
  readRangeSq: 3.2 * 3.2,
  message: `"The crystal sings, and its song becomes memory — you grow wiser."  (+${XP_REWARD} XP)`,
  onRead: (state, player) => addXp(state, player, XP_REWARD),
});

export const start = shrine.start;
export const update = shrine.update;
