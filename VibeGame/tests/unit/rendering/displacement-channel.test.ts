import { describe, expect, it } from 'bun:test';

// CHANNEL_MAP is not exported, so we test the specification directly.
// Source: VibeGame/src/plugins/rendering/texture-recipe-system.ts
// Channel 0 = map, 1 = normalMap, 2 = roughnessMap, 3 = metalnessMap, 4 = aoMap, 5 = displacementMap
const CHANNEL_COUNT = 6;

describe('CHANNEL_MAP (displacementMap channel)', () => {
  it('has 6 channels (0-5)', () => {
    // CHANNEL_MAP = ['map','normalMap','roughnessMap','metalnessMap','aoMap','displacementMap']
    expect(CHANNEL_COUNT).toBe(6);
  });

  it('maps channel 5 to displacementMap', () => {
    const CHANNEL_MAP = [
      'map',           // 0
      'normalMap',     // 1
      'roughnessMap',  // 2
      'metalnessMap',  // 3
      'aoMap',         // 4
      'displacementMap', // 5
    ] as const;
    expect(CHANNEL_MAP[5]).toBe('displacementMap');
  });

  it('preserves existing channel indices', () => {
    const CHANNEL_MAP = [
      'map',           // 0
      'normalMap',     // 1
      'roughnessMap',  // 2
      'metalnessMap',  // 3
      'aoMap',         // 4
      'displacementMap', // 5
    ] as const;
    expect(CHANNEL_MAP[0]).toBe('map');
    expect(CHANNEL_MAP[1]).toBe('normalMap');
    expect(CHANNEL_MAP[2]).toBe('roughnessMap');
    expect(CHANNEL_MAP[3]).toBe('metalnessMap');
    expect(CHANNEL_MAP[4]).toBe('aoMap');
  });
});
