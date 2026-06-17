// Ancient rune pillar (stone_pillar.glb). Glows with an arcane pulse until read;
// pressing F grants skill points (spend them in the pause menu) and the glow dies.
import { createMysticObject } from '../game/mystic.ts';
import { addSkillPoints } from '../game/skills.ts';

const SKILL_POINTS = 2;

const pillar = createMysticObject({
  modelUrl: '/assets/meshes/stone_pillar.glb',
  emissiveColor: 0x8a5cff,
  toastColor: '#c9a6ff',
  readRangeSq: 3.4 * 3.4,
  message: `"The runes drink your gaze and answer in light — power stirs within you."  (+${SKILL_POINTS} skill points)`,
  onRead: () => addSkillPoints(SKILL_POINTS),
});

export const start = pillar.start;
export const update = pillar.update;
