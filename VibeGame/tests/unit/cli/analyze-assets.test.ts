import { mkdirSync, writeFileSync } from 'node:fs';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import * as path from 'node:path';
import { describe, expect, it } from 'bun:test';
import type { ParsedElement } from '../../../src/core';
import {
  checkAssetUrls,
  lod0SiblingUrl,
} from '../../../src/cli/analyze/assets';

function el(
  tagName: string,
  attributes: Record<string, string> = {},
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

describe('checkAssetUrls', () => {
  it('flags missing GLB url and image attrs (heightmap/texture/icon)', () => {
    const pub = mkdtempSync(path.join(tmpdir(), 'vg-analyze-'));
    mkdirSync(path.join(pub, 'assets', 'meshes'), { recursive: true });
    writeFileSync(path.join(pub, 'assets', 'meshes', 'ok.glb'), 'x');

    const root = el('world', {}, [
      el('GLTFLoader', { url: '/assets/meshes/ok.glb' }),
      el('GLTFLoader', { url: '/assets/meshes/missing.glb' }),
      el('PlayerGLTF', { 'model-url': '/assets/meshes/hero_missing.glb' }),
      el('Terrain', {
        heightmap: '/assets/terrain/missing_hm.png',
        texture: '/assets/textures/missing_tex.png',
      }),
      el('StatBar', { icon: '/assets/icons/missing_icon.png' }),
      el('BiomeRegion', {
        'terrain-texture': '/assets/textures/missing_biome.png',
      }),
    ]);

    const issues = checkAssetUrls(root, pub);
    const msgs = issues.map((i) => i.message);
    expect(
      msgs.some((m) => m.includes('missing.glb') && m.includes('url'))
    ).toBe(true);
    expect(
      msgs.some(
        (m) => m.includes('hero_missing.glb') && m.includes('model-url')
      )
    ).toBe(true);
    expect(
      msgs.some((m) => m.includes('missing_hm.png') && m.includes('heightmap'))
    ).toBe(true);
    expect(
      msgs.some((m) => m.includes('missing_tex.png') && m.includes(' texture>'))
    ).toBe(true);
    expect(
      msgs.some((m) => m.includes('missing_icon.png') && m.includes('icon'))
    ).toBe(true);
    expect(
      msgs.some(
        (m) => m.includes('missing_biome.png') && m.includes('terrain-texture')
      )
    ).toBe(true);
    expect(msgs.some((m) => m.includes('ok.glb'))).toBe(false);
  });

  it('splits vegetation meshes= list and reports each missing path', () => {
    const pub = mkdtempSync(path.join(tmpdir(), 'vg-analyze-'));
    mkdirSync(path.join(pub, 'assets', 'meshes', 'vegetation'), {
      recursive: true,
    });
    writeFileSync(
      path.join(pub, 'assets', 'meshes', 'vegetation', 'grass.glb'),
      'x'
    );

    const root = el('world', {}, [
      el('VegetationHub', {
        meshes:
          '/assets/meshes/vegetation/grass.glb /assets/meshes/vegetation/missing_leaf.glb',
      }),
    ]);
    const issues = checkAssetUrls(root, pub);
    const msgs = issues.map((i) => i.message);
    expect(
      msgs.some((m) => m.includes('missing_leaf.glb') && m.includes('meshes'))
    ).toBe(true);
    expect(msgs.some((m) => m.includes('grass.glb'))).toBe(false);
  });

  it('resolves relative asset paths under publicDir', () => {
    const pub = mkdtempSync(path.join(tmpdir(), 'vg-analyze-'));
    mkdirSync(path.join(pub, 'assets', 'meshes'), { recursive: true });
    writeFileSync(path.join(pub, 'assets', 'meshes', 'ok.glb'), 'x');

    const root = el('world', {}, [
      el('GLTFLoader', { url: 'assets/meshes/ok.glb' }),
      el('GLTFLoader', { url: 'assets/meshes/gone.glb' }),
    ]);
    const issues = checkAssetUrls(root, pub);
    const msgs = issues.map((i) => i.message);
    expect(msgs.some((m) => m.includes('gone.glb'))).toBe(true);
    expect(msgs.some((m) => m.includes('ok.glb'))).toBe(false);
  });

  it('lod0SiblingUrl maps lodN→lod0', () => {
    expect(lod0SiblingUrl('/assets/meshes/moss_rock_lod1.glb')).toBe(
      '/assets/meshes/moss_rock_lod0.glb'
    );
    expect(lod0SiblingUrl('/assets/meshes/moss_rock_lod0.glb')).toBe(null);
  });

  it('warns when url aliases lod1-url or skips existing lod0', () => {
    const pub = mkdtempSync(path.join(tmpdir(), 'vg-analyze-'));
    mkdirSync(path.join(pub, 'assets', 'meshes'), { recursive: true });
    writeFileSync(path.join(pub, 'assets', 'meshes', 'rock_lod0.glb'), 'x');
    writeFileSync(path.join(pub, 'assets', 'meshes', 'rock_lod1.glb'), 'x');
    writeFileSync(path.join(pub, 'assets', 'meshes', 'rock_lod2.glb'), 'x');

    const root = el('world', {}, [
      el('GLTFLoader', {
        url: '/assets/meshes/rock_lod1.glb',
        'lod1-url': '/assets/meshes/rock_lod1.glb',
        'lod2-url': '/assets/meshes/rock_lod2.glb',
      }),
      el('GLTFLoader', {
        url: '/assets/meshes/rock_lod0.glb',
        'lod1-url': '/assets/meshes/rock_lod1.glb',
        'lod2-url': '/assets/meshes/rock_lod2.glb',
      }),
    ]);
    const msgs = checkAssetUrls(root, pub).map((i) => i.message);
    expect(msgs.some((m) => m.includes('aliases lod1-url'))).toBe(true);
    expect(msgs.some((m) => m.includes('skips existing'))).toBe(true);
    expect(
      msgs.filter((m) => m.includes('rock_lod0.glb') && m.includes('missing'))
        .length
    ).toBe(0);
  });
});
