import { describe, expect, test } from 'bun:test';

import { pickLodLevel } from '../../src/plugins/gltf-xml/gltf-lod-level';

describe('pickLodLevel', () => {
  test('near and mid boundaries', () => {
    expect(pickLodLevel(0, 40, 120)).toBe(0);
    expect(pickLodLevel(39.9, 40, 120)).toBe(0);
    expect(pickLodLevel(40, 40, 120)).toBe(1);
    expect(pickLodLevel(119.9, 40, 120)).toBe(1);
    expect(pickLodLevel(120, 40, 120)).toBe(2);
    expect(pickLodLevel(500, 40, 120)).toBe(2);
  });
});
