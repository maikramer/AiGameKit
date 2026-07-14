import { describe, expect, it } from 'bun:test';
import { GltfXmlPlugin } from '../../../src/plugins/gltf-xml/plugin';

describe('GltfXmlPlugin lod defaults', () => {
  it('activeLevel inicial é lod0', () => {
    expect(GltfXmlPlugin.config?.defaults?.gltfLod?.activeLevel).toBe(0);
  });
});
