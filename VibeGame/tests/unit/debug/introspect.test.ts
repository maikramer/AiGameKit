import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { TransformsPlugin, Transform } from '../../../src/plugins/transforms';
import { DebugPlugin } from '../../../src/plugins/debug/plugin';
import { setScriptFile } from '../../../src/plugins/entity-script/context';
import { MonoBehaviour } from '../../../src/plugins/entity-script/components';

/**
 * The spatial/script introspection APIs on the debug bridge let agents ask
 * "what is near this point" and "which scripts resolved" without ad-hoc
 * page scripts. These tests lock the shape and the world/local fallback.
 */
describe('debug bridge introspection', () => {
  const originalWindow = globalThis.window;

  let state: State;
  let bridge: Record<string, any>;

  beforeEach(() => {
    (globalThis as Record<string, unknown>).window = {} as Window;
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerComponent('mono-behaviour', MonoBehaviour);
    DebugPlugin.initialize!(state);
    bridge = (
      globalThis.window as unknown as { __VIBEGAME__: Record<string, any> }
    ).__VIBEGAME__;
  });

  afterEach(() => {
    (globalThis as Record<string, unknown>).window = originalWindow;
  });

  function makeEntity(name: string | null, x: number, y: number, z: number) {
    const eid = state.createEntity();
    if (name) state.setEntityName(name, eid);
    state.addComponent(eid, Transform, { posX: x, posY: y, posZ: z });
    return eid;
  }

  it('position() prefers world-transform and falls back to local', () => {
    const eid = makeEntity('well', -6, 100, 4);
    expect(bridge.position('well')).toEqual({ x: -6, y: 100, z: 4 });
    expect(bridge.position(eid)).toEqual({ x: -6, y: 100, z: 4 });
    expect(bridge.position(eid)).not.toBeNull();

    // A world-transform overrides the local values once present.
    const WorldTransform = state.getComponent('world-transform')!;
    state.addComponent(eid, WorldTransform);
    const wt = WorldTransform as unknown as Record<string, number[]>;
    wt.posX[eid] = 10;
    wt.posY[eid] = 133.6;
    wt.posZ[eid] = -20;
    const wtPos = bridge.position('well');
    expect(wtPos.x).toBe(10);
    expect(wtPos.y).toBeCloseTo(133.6);
    expect(wtPos.z).toBe(-20);
  });

  it('position() returns null for unknown entities', () => {
    expect(bridge.position('nope')).toBeNull();
    expect(bridge.position(9999)).toBeNull();
  });

  it('entitiesNear() filters by radius, sorts by distance, honours with/limit', () => {
    makeEntity('near', 1, 100, 1);
    makeEntity('far', 30, 100, 30);
    makeEntity('mid', 5, 100, 2);
    makeEntity('torch', 2, 100, 2);

    const rows = bridge.entitiesNear(0, 0, 10);
    expect(rows.map((r: any) => r.name)).toEqual(['near', 'torch', 'mid']);
    expect(rows[0].dist).toBeLessThan(rows[1].dist);
    expect(rows[0].components).toContain('transform');

    const withMissing = bridge.entitiesNear(0, 0, 100, { with: ['gltf-lod'] });
    expect(withMissing).toEqual([]);

    const limited = bridge.entitiesNear(0, 0, 100, { limit: 2 });
    expect(limited.length).toBe(2);
  });

  it('scripts() reports file resolution and lifecycle state', () => {
    const eid = makeEntity('npc', 0, 0, 0);
    state.addComponent(eid, MonoBehaviour, { ready: 1, enabled: 1 });

    // Unresolved file (glob miss) must still be listed — that is the
    // failure signature this API exists to expose.
    expect(bridge.scripts()).toEqual([
      { eid, name: 'npc', file: null, ready: true, enabled: true },
    ]);

    setScriptFile(state, eid, './merchant.ts');
    expect(bridge.scripts()[0].file).toBe('./merchant.ts');
  });
});
