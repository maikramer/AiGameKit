import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { TransformsPlugin } from 'vibegame/transforms';
import { SpawnerPlugin } from '../../../src/plugins/spawner/plugin';
import { GltfXmlPlugin } from '../../../src/plugins/gltf-xml/plugin';
import { VegetationPlugin } from '../../../src/plugins/vegetation/plugin';
import { Vegetation } from '../../../src/plugins/vegetation/components';
import {
  parseVegetationMeshes,
  toBoolAttr,
} from '../../../src/plugins/vegetation/parse-meshes';
import {
  getVegetationWindUrls,
  _resetVegetationWindUrls,
} from '../../../src/plugins/vegetation/wind';
import { getSpawnGroupSpecs } from '../../../src/plugins/spawner/context';
import { SpawnerPending } from '../../../src/plugins/spawner/components';

describe('parseVegetationMeshes', () => {
  it('splits space and comma separated URLs', () => {
    expect(parseVegetationMeshes('/a.glb /b.glb,/c.glb')).toEqual([
      '/a.glb',
      '/b.glb',
      '/c.glb',
    ]);
  });

  it('returns empty for blank', () => {
    expect(parseVegetationMeshes('')).toEqual([]);
    expect(parseVegetationMeshes(null)).toEqual([]);
  });
});

describe('toBoolAttr', () => {
  it('parses common truthy/falsy forms', () => {
    expect(toBoolAttr('1', false)).toBe(true);
    expect(toBoolAttr('false', true)).toBe(false);
    expect(toBoolAttr(undefined, true)).toBe(true);
  });
});

describe('Vegetation recipe', () => {
  beforeEach(() => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;
  });

  function boot(): State {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(GltfXmlPlugin);
    state.registerPlugin(SpawnerPlugin);
    state.registerPlugin(VegetationPlugin);
    return state;
  }

  it('parses meshes + density into a static SpawnGroupSpec', () => {
    const state = boot();
    _resetVegetationWindUrls(state);
    const xml = `<root>
      <Vegetation
        meshes="/assets/meshes/vegetation/grass.glb /assets/meshes/vegetation/grass_large.glb"
        density-per-km2="15000"
        seed="9"
        region-min="-20 0 -20"
        region-max="20 0 20"
        wind="1"
      ></Vegetation>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const entities = parseXMLToEntities(state, parsed.root);
    expect(entities).toHaveLength(1);
    const eid = entities[0]!.entity;
    expect(Vegetation.wind[eid]).toBe(1);
    expect(state.hasComponent(eid, SpawnerPending)).toBe(true);

    const spec = getSpawnGroupSpecs(state).get(eid);
    expect(spec).toBeDefined();
    expect(spec!.mode).toBe('static');
    expect(spec!.spawnCountMode).toBe('density');
    expect(spec!.densityPerKm2).toBe(15000);
    expect(spec!.seed).toBe(9);
    expect(spec!.avoidOverlaps).toBe(false);
    expect(spec!.templates).toHaveLength(2);
    expect(spec!.templates[0]!.attributes.url).toBe(
      '/assets/meshes/vegetation/grass.glb'
    );
    expect(spec!.templates[0]!.attributes.instanced).toBe('true');

    const windUrls = getVegetationWindUrls(state);
    expect(windUrls.has('/assets/meshes/vegetation/grass.glb')).toBe(true);
    expect(windUrls.has('/assets/meshes/vegetation/grass_large.glb')).toBe(
      true
    );
  });

  it('wind="0" skips wind URL registration', () => {
    const state = boot();
    _resetVegetationWindUrls(state);
    const xml = `<root>
      <Vegetation
        meshes="/assets/meshes/vegetation/grass.glb"
        count="5"
        region-min="0 0 0"
        region-max="10 0 10"
        wind="0"
      ></Vegetation>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const [e] = parseXMLToEntities(state, parsed.root);
    expect(Vegetation.wind[e!.entity]).toBe(0);
    expect(getVegetationWindUrls(state).size).toBe(0);
  });

  it('throws when meshes is missing', () => {
    const state = boot();
    const xml =
      '<root><Vegetation density-per-km2="1000" region-min="0 0 0" region-max="1 0 1"></Vegetation></root>';
    const parsed = XMLParser.parse(xml);
    expect(() => parseXMLToEntities(state, parsed.root)).toThrow(/meshes/);
  });
});
