// Ancient rune pillar (stone_pillar.glb). Glows with an arcane pulse until read;
// pressing F grants skill points (spend them in the pause menu) and the glow dies.
import { createMysticObject } from '../game/mystic.ts';
import { addSkillPoints } from '../game/skills.ts';

const SKILL_POINTS = 2;

const pillar = createMysticObject({
  modelUrl: '/assets/meshes/props/stone_pillar_lod2.glb',
  emissiveColor: 0x8a5cff,
  toastColor: '#c9a6ff',
  readRangeSq: 4.6 * 4.6,
  promptLabel: 'Receber poder',
  message: `"As runas bebem o seu olhar e respondem em luz — o poder se mexe em você."  (+${SKILL_POINTS} pontos de habilidade)`,
  onRead: () => addSkillPoints(SKILL_POINTS),
});

export const start = pillar.start;
export const update = pillar.update;
