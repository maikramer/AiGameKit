// Glowing mushroom (mushroom_red.glb). A faintly pulsing forage; reading it (F)
// restores health once, then it stops glowing.
import { createMysticObject } from '../game/mystic.ts';
import { healHealth } from 'vibegame';

const HEAL = 40;

const mushroom = createMysticObject({
  modelUrl: '/assets/meshes/mushroom_red_lod2.glb',
  emissiveColor: 0xff5a6a,
  toastColor: '#ffb0a0',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Comer cogumelo',
  gesture: 'gather',
  emissiveBase: 0.25,
  emissivePulse: 0.3,
  message: `"Você esmaga o chapéu — o calor se espalha pelos membros cansados."  (+${HEAL} HP)`,
  onRead: (_state, player) => healHealth(player, HEAL),
});

export const start = mushroom.start;
export const update = mushroom.update;
