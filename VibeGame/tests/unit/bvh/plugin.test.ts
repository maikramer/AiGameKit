import { describe, expect, it } from 'bun:test';
import { beforeEach } from 'bun:test';
import { State } from 'vibegame';
import { BvhPlugin, BvhTarget } from 'vibegame';

describe('BvhPlugin shape', () => {
  it('registra dois sistemas (terrain + static mesh) no grupo simulation', () => {
    expect(Array.isArray(BvhPlugin.systems)).toBe(true);
    expect(BvhPlugin.systems).toHaveLength(2);
    for (const system of BvhPlugin.systems!) {
      expect(system.group).toBe('simulation');
      expect(typeof system.update).toBe('function');
    }
  });

  it('ordena o static mesh sync depois do terrain sync e detém o dispose', () => {
    const [terrainSync, staticMeshSync] = BvhPlugin.systems!;
    expect(Array.isArray(staticMeshSync.after)).toBe(true);
    expect(staticMeshSync.after).toContain(terrainSync);
    expect(typeof staticMeshSync.dispose).toBe('function');
    expect(terrainSync.dispose).toBeUndefined();
  });

  it('expõe defaults de bvh-target (include/layer/dirty)', () => {
    const defaults = BvhPlugin.config?.defaults?.['bvh-target'];
    expect(defaults).toBeDefined();
    expect(defaults!.include).toBe(1);
    expect(defaults!.layer).toBe(0xffff);
    expect(defaults!.dirty).toBe(1);
  });

  it('mapeia o componente BvhTarget no registro do plugin', () => {
    expect(BvhPlugin.components).toBeDefined();
    expect(Object.values(BvhPlugin.components!)).toContain(BvhTarget);
  });
});

describe('BvhPlugin integração com State', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  it('registerPlugin torna BvhTarget adicionável e visível por hasComponent', () => {
    state.registerPlugin(BvhPlugin);
    const entity = state.createEntity();
    state.addComponent(entity, BvhTarget);

    expect(state.hasComponent(entity, BvhTarget)).toBe(true);
  });
});
