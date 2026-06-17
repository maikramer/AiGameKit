// Glowing mushroom (mushroom_red.glb). A faintly pulsing forage; reading it (F)
// restores health once, then it stops glowing.
import { createMysticObject } from '../game/mystic.ts';
import { healHealth } from 'vibegame';

const HEAL = 40;

const mushroom = createMysticObject({
  modelUrl: '/assets/meshes/mushroom_red.glb',
  emissiveColor: 0xff5a6a,
  toastColor: '#ffb0a0',
  readRangeSq: 2.8 * 2.8,
  emissiveBase: 0.25,
  emissivePulse: 0.3,
  message: `"You crush the cap — warmth spreads through tired limbs."  (+${HEAL} HP)`,
  onRead: (_state, player) => healHealth(player, HEAL),
});

export const start = mushroom.start;
export const update = mushroom.update;
