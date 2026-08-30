// A Fonte da Clareira — água cristalina no coração do brejo (-540,30).
// Beber (F) cura tudo de graça, uma vez só: o refúgio recompensa quem
// atravessou o pântano até aqui.
import { createMysticObject } from '../../game/mystic.ts';
import { healHealth, Health } from 'vibegame';

const spring = createMysticObject({
  modelUrl: '/assets/meshes/props/crystal_blue_lod1.glb',
  modelScale: 1.1,
  emissiveColor: 0x7fffe0,
  toastColor: '#a8ffe8',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Beber da fonte',
  message:
    '"A água não curte o brejo — o brejo é que a respeita. Bebe: ela devolve o que o pântano tomou."  (vida restaurada)',
  onRead: (state, player) => {
    const max = Health.max[player] ?? 0;
    if (max > 0) healHealth(player, max - (Health.current[player] ?? 0));
  },
});

export const start = spring.start;
export const update = spring.update;
