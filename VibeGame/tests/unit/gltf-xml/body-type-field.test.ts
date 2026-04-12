import { describe, expect, it } from 'bun:test';
import { GltfPhysicsPending } from '../../../src/plugins/gltf-xml/components';
import { GltfXmlPlugin } from '../../../src/plugins/gltf-xml/plugin';

describe('GltfPhysicsPending bodyType field', () => {
  it('should have bodyType field of type ui8', () => {
    expect(GltfPhysicsPending.bodyType).toBeDefined();
    expect(typeof GltfPhysicsPending.bodyType[0]).toBe('number');
  });

  it('should default bodyType to 0 when component is added', () => {
    const eid = 0;
    expect(GltfPhysicsPending.bodyType[eid]).toBe(0);
  });

  it('should roundtrip write/read for values 0, 1, 2, 3', () => {
    for (const val of [0, 1, 2, 3]) {
      const eid = val;
      GltfPhysicsPending.bodyType[eid] = val;
      expect(GltfPhysicsPending.bodyType[eid]).toBe(val);
    }
  });
});

describe('GltfXmlPlugin bodyType config', () => {
  it('should have bodyType enum with correct mappings', () => {
    const enums = GltfXmlPlugin.config!.enums!.gltfPhysicsPending;
    expect(enums.bodyType).toBeDefined();
    expect(enums.bodyType.dynamic).toBe(0);
    expect(enums.bodyType.fixed).toBe(1);
    expect(enums.bodyType['kinematic-position']).toBe(2);
    expect(enums.bodyType['kinematic-velocity']).toBe(3);
  });

  it('should have default bodyType of 0 in gltfPhysicsPending defaults', () => {
    const defaults = GltfXmlPlugin.config!.defaults!.gltfPhysicsPending;
    expect(defaults.bodyType).toBe(0);
  });
});
