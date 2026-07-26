import { describe, expect, it } from 'bun:test';
import { templateVisualUrl } from '../../../src/plugins/spawner/template-url';
import type { SpawnTemplateSpec } from '../../../src/plugins/spawner/types';

describe('templateVisualUrl', () => {
  it('reads url on the template itself', () => {
    const tpl: SpawnTemplateSpec = {
      tagName: 'GLTFLoader',
      attributes: { url: '/assets/meshes/tree.glb' },
      role: 'visual',
    };
    expect(templateVisualUrl(tpl)).toBe('/assets/meshes/tree.glb');
  });

  it('walks GameObject → GLTFLoader child (enemy/NPC pattern)', () => {
    const tpl: SpawnTemplateSpec = {
      tagName: 'GameObject',
      attributes: {},
      role: 'enemy',
      entityChildren: [
        {
          tagName: 'GLTFLoader',
          attributes: { url: '/assets/meshes/goblin_lod2.glb' },
          children: [],
        },
      ],
    };
    expect(templateVisualUrl(tpl)).toBe('/assets/meshes/goblin_lod2.glb');
  });

  it('returns empty when neither template nor children have a url', () => {
    const tpl: SpawnTemplateSpec = {
      tagName: 'GameObject',
      attributes: {},
      role: 'enemy',
      entityChildren: [
        { tagName: 'ResourceNode', attributes: {}, children: [] },
      ],
    };
    expect(templateVisualUrl(tpl)).toBe('');
  });
});
