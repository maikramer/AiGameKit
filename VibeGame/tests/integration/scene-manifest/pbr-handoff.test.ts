import { describe, expect, it } from 'bun:test';
import type { HandoffRow } from '../../../src/plugins/scene-manifest/loader';

describe('HandoffRow pbr_textures (T9)', () => {
  it('pbr_textures is optional', () => {
    const row: HandoffRow = { id: 'test', model: { url: '/test.glb' } };
    expect(row.pbr_textures).toBeUndefined();
  });

  it('accepts string array for pbr_textures', () => {
    const row: HandoffRow = {
      id: 'test',
      model: { url: '/test.glb' },
      pbr_textures: ['/assets/pbr/test/normal.png', '/assets/pbr/test/roughness.png'],
    };
    expect(row.pbr_textures).toHaveLength(2);
    expect(row.pbr_textures![0]).toBe('/assets/pbr/test/normal.png');
  });

  it('accepts up to 5 pbr textures (channels 0-4)', () => {
    const row: HandoffRow = {
      id: 'test',
      model: { url: '/test.glb' },
      pbr_textures: [
        '/assets/pbr/test/map.png',
        '/assets/pbr/test/normal.png',
        '/assets/pbr/test/roughness.png',
        '/assets/pbr/test/metalness.png',
        '/assets/pbr/test/ao.png',
      ],
    };
    expect(row.pbr_textures).toHaveLength(5);
  });
});
