import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { GltfLod, GltfPending } from '../../../src/plugins/gltf-xml/components';
import {
  getGltfLodUrls,
  getGltfUrl,
} from '../../../src/plugins/gltf-xml/context';
import { getInstancedLodUrls } from '../../../src/plugins/gltf-xml/auto-instance';
import { GltfXmlPlugin } from '../../../src/plugins/gltf-xml/plugin';

describe('lod1-url / lod2-url promote non-instanced GltfLod', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
    state.registerPlugin(GltfXmlPlugin);
  });

  it('adapters turn url+lod1+lod2 into a GltfLod triple', () => {
    const eid = state.createEntity();
    state.addComponent(eid, GltfPending);

    const cfg = GltfXmlPlugin.config!.adapters!.gltfPending as Record<
      string,
      (entity: number, value: string, state: State) => void
    >;

    cfg.url(eid, '/assets/meshes/goblin_lod0.glb', state);
    cfg['lod1-url'](eid, '/assets/meshes/goblin_lod1.glb', state);
    cfg['lod2-url'](eid, '/assets/meshes/goblin_lod2.glb', state);
    cfg['lod-threshold-near'](eid, '20', state);
    cfg['lod-threshold-mid'](eid, '50', state);

    expect(getGltfUrl(state, eid)).toBe('/assets/meshes/goblin_lod0.glb');
    expect(getInstancedLodUrls(state, eid)).toEqual([
      '/assets/meshes/goblin_lod1.glb',
      '/assets/meshes/goblin_lod2.glb',
    ]);
    expect(getGltfLodUrls(state, eid)).toEqual([
      '/assets/meshes/goblin_lod0.glb',
      '/assets/meshes/goblin_lod1.glb',
      '/assets/meshes/goblin_lod2.glb',
    ]);
    expect(state.hasComponent(eid, GltfLod)).toBe(true);
    expect(GltfLod.thresholdNear[eid]).toBe(20);
    expect(GltfLod.thresholdMid[eid]).toBe(50);
  });
});
