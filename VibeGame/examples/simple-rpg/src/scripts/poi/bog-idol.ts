// Ídolo do brejo — recompensa do altar de ossos do Pântano (-48, -132).
// Tocar (F) cura o herói por inteiro: é o único ponto de cura entre a ponte
// sul e o Bog Warden. Módulo próprio por POI: ver nota em poi/watch-tome.ts.
import { createMysticObject } from '../../game/mystic.ts';
import { healHealth } from 'vibegame';

const HEAL_REWARD = 70;

const idol = createMysticObject({
  modelUrl: '/assets/meshes/crystal_blue_lod1.glb',
  modelScale: 2.0,
  emissiveColor: 0x66e878,
  toastColor: '#a8ffb0',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Tocar no ídolo do brejo',
  message: `"A pedra está morna. O que o brejo tirou dos outros, devolve pra você."  (+${HEAL_REWARD} HP)`,
  onRead: (_state, player) => healHealth(player, HEAL_REWARD),
});

export const start = idol.start;
export const update = idol.update;
