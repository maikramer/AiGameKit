import { describe, expect, it } from 'bun:test';
import {
  PRESET_NAMES,
  createPresetParams,
  presetIndex,
} from '../../../src/plugins/particles/presets';
import {
  PRESET_TEXTURE_FILE,
  createSoftCircleTexture,
  getParticleTexture,
  particleMaterial,
} from '../../../src/plugins/particles/textures';

describe('particle textures', () => {
  it('maps every preset to a sprite filename', () => {
    for (const name of PRESET_NAMES) {
      expect(PRESET_TEXTURE_FILE[name]).toMatch(/\.png$/);
    }
  });

  it('soft circle fallback has transparent rim', () => {
    const tex = createSoftCircleTexture(32);
    expect(tex.image.width).toBe(32);
    expect(tex.image.height).toBe(32);
    const data = tex.image.data as Uint8Array;
    // Corner pixel alpha ≈ 0
    expect(data[3]).toBeLessThan(20);
    // Center pixel alpha high
    const mid = (16 * 32 + 16) * 4 + 3;
    expect(data[mid]).toBeGreaterThan(200);
  });

  it('headless getParticleTexture returns a map-ready texture', () => {
    const tex = getParticleTexture('fire');
    expect(tex).toBeDefined();
    expect(tex.image).toBeTruthy();
  });

  it('particleMaterial attaches a map', () => {
    const mat = particleMaterial({ preset: 'smoke', opacity: 0.5 });
    expect(mat.map).toBeTruthy();
    expect(mat.transparent).toBe(true);
    expect(mat.depthWrite).toBe(false);
  });

  it('every preset factory includes a textured material', () => {
    for (const name of PRESET_NAMES) {
      const params = createPresetParams(name);
      expect(params.material).toBeDefined();
      expect((params.material as { map?: unknown }).map).toBeTruthy();
    }
  });

  it('sparkle aliases to magic', () => {
    expect(presetIndex('sparkle')).toBe(presetIndex('magic'));
  });
});
