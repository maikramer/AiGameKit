import { describe, expect, it } from 'bun:test';
import { normalizeInstancedLodUrls } from '../../../src/plugins/gltf-xml/auto-instance';

describe('normalizeInstancedLodUrls', () => {
  it('keeps distinct lod ladder', () => {
    expect(
      normalizeInstancedLodUrls('/a_lod0.glb', '/a_lod1.glb', '/a_lod2.glb')
    ).toEqual(['/a_lod0.glb', '/a_lod1.glb', '/a_lod2.glb']);
  });

  it('drops lod1 when it aliases url (near-vanish bug)', () => {
    expect(
      normalizeInstancedLodUrls(
        '/dead_tree_lod1.glb',
        '/dead_tree_lod1.glb',
        '/dead_tree_lod2.glb'
      )
    ).toEqual(['/dead_tree_lod1.glb', undefined, '/dead_tree_lod2.glb']);
  });

  it('drops lod2 when it aliases lod1 or url', () => {
    expect(normalizeInstancedLodUrls('/a.glb', '/b.glb', '/b.glb')).toEqual([
      '/a.glb',
      '/b.glb',
      undefined,
    ]);
    expect(normalizeInstancedLodUrls('/a.glb', '/b.glb', '/a.glb')).toEqual([
      '/a.glb',
      '/b.glb',
      undefined,
    ]);
  });
});
