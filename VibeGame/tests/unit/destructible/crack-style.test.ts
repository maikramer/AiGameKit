import { MeshStandardMaterial } from 'three';
import { describe, expect, it } from 'bun:test';
import {
  CRACK_STYLE_VERTICAL,
  CRACK_STYLE_VORONOI,
  makeCrackMaterial,
} from '../../../src/plugins/destructible/fx';

/**
 * The crack overlay compiles a different shader per style. THREE's program
 * cache keys by `customProgramCacheKey()`, so the key MUST differ across styles
 * — otherwise a voronoi prop can be handed the vertical shader (or vice-versa).
 */
describe('destructible crack material — program cache key varies by style', () => {
  it('uses distinct cache keys for voronoi vs vertical', () => {
    const base = new MeshStandardMaterial({ color: 0x808080 });
    const uniform = { value: 0.5 };
    const voronoi = makeCrackMaterial(base, uniform, CRACK_STYLE_VORONOI);
    const vertical = makeCrackMaterial(base, uniform, CRACK_STYLE_VERTICAL);

    const keyVoronoi = voronoi.customProgramCacheKey?.() ?? '';
    const keyVertical = vertical.customProgramCacheKey?.() ?? '';

    expect(keyVoronoi).not.toBe(keyVertical);
    expect(keyVoronoi).toContain(String(CRACK_STYLE_VORONOI));
    expect(keyVertical).toContain(String(CRACK_STYLE_VERTICAL));

    voronoi.dispose();
    vertical.dispose();
    base.dispose();
  });

  it('is stable for the same style across calls', () => {
    const base = new MeshStandardMaterial({ color: 0x808080 });
    const uniform = { value: 0 };
    const a = makeCrackMaterial(base, uniform, CRACK_STYLE_VERTICAL);
    const b = makeCrackMaterial(base, uniform, CRACK_STYLE_VERTICAL);
    expect(a.customProgramCacheKey?.()).toBe(b.customProgramCacheKey?.());
    a.dispose();
    b.dispose();
    base.dispose();
  });
});
