import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'aigamekit-vibegame';
import { TransformsPlugin } from 'aigamekit-vibegame/transforms';
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
import {
  getVegetationPatch,
  _resetVegetationPatches,
} from '../../../src/plugins/vegetation/patch-context';
import { spawnSpecFromLayer } from '../../../src/plugins/vegetation/spec-from-plan';
import { buildVegetationPlan } from '../../../src/plugins/vegetation/plan';
import { _resetVegetationHubs } from '../../../src/plugins/vegetation/hubs';

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
    expect(spec!.variation.preset).toBe('foliage');

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

  it('smart="0" keeps legacy single SpawnGroupSpec', () => {
    const state = boot();
    _resetVegetationWindUrls(state);
    _resetVegetationPatches(state);
    const xml = `<root>
      <Vegetation
        meshes="/assets/meshes/vegetation/grass.glb /assets/meshes/vegetation/flower_yellowA.glb"
        density-per-km2="12000"
        smart="0"
        region-min="-5 0 -5"
        region-max="5 0 5"
      ></Vegetation>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const [e] = parseXMLToEntities(state, parsed.root);
    const eid = e!.entity;
    expect(SpawnerPending.spawned[eid]).toBe(0);
    const spec = getSpawnGroupSpecs(state).get(eid);
    expect(spec).toBeDefined();
    expect(spec!.templates).toHaveLength(2);
    expect(getVegetationPatch(state, eid)?.plan.smart).toBe(false);
  });

  it('smart multi-role defers spawn to planner (parent marked spawned)', () => {
    const state = boot();
    _resetVegetationWindUrls(state);
    _resetVegetationPatches(state);
    _resetVegetationHubs(state);
    const xml = `<root>
      <Vegetation
        meshes="/assets/meshes/vegetation/grass.glb /assets/meshes/vegetation/flower_yellowA.glb"
        density-per-km2="20000"
        seed="3"
        region-min="-8 0 -8"
        region-max="8 0 8"
        cluster-count="10"
      ></Vegetation>
    </root>`;
    const parsed = XMLParser.parse(xml);
    const [e] = parseXMLToEntities(state, parsed.root);
    const eid = e!.entity;
    expect(SpawnerPending.spawned[eid]).toBe(1);
    expect(getSpawnGroupSpecs(state).has(eid)).toBe(false);
    const patch = getVegetationPatch(state, eid);
    expect(patch?.plan.smart).toBe(true);
    expect(patch?.hubsReady).toBe(false);
    expect(patch?.layerEntities).toHaveLength(0);
    expect(
      getVegetationWindUrls(state).has(
        '/assets/meshes/vegetation/flower_yellowA.glb'
      )
    ).toBe(true);
  });
});

describe('spawnSpecFromLayer clusterCenters', () => {
  it('embeds shared hubs for flower layer', () => {
    const plan = buildVegetationPlan({
      meshes: ['/v/grass.glb', '/v/flower_redA.glb'],
      smart: true,
      seed: 1,
      regionMin: [-1, 0, -1],
      regionMax: [1, 0, 1],
      clusterCount: 4,
      clusterRadius: 3,
      flowerNearRadius: 2,
      flowerDensityRatio: 0.15,
      plantDensityRatio: 0.25,
      wind: true,
      avoidWater: true,
      avoidRoad: true,
      avoidOverlaps: false,
      maxSlopeDeg: 35,
      maxDistance: 100,
      footprintRadius: 0.2,
      spawnCountMode: 'density',
      densityPerKm2: 10000,
      count: 0,
      patchScaleMin: null,
      patchScaleMax: null,
      scaleAxisMin: 0.9,
      scaleAxisMax: 1.1,
      variation: {
        preset: 'foliage' as const,
        hueJitterDeg: 8,
        saturationMin: 0.9,
        saturationMax: 1.12,
        brightnessMin: 0.88,
        brightnessMax: 1.14,
        contrastMin: 0.92,
        contrastMax: 1.1,
        spatial: 0.45,
      },
    });
    const flower = plan.layers.find((l) => l.role === 'flower')!;
    const hubs: Array<[number, number]> = [
      [1, 2],
      [3, 4],
    ];
    const spec = spawnSpecFromLayer(plan, flower, hubs);
    expect(spec.clusterCenters).toEqual(hubs);
    expect(spec.clusterRadius).toBe(2);
    expect(spec.densityPerKm2).toBeCloseTo(1500);
    expect(spec.scaleAxisMin).toBe(0.9);
    expect(spec.scaleAxisMax).toBe(1.1);
    expect(spec.variation.preset).toBe('foliage');
  });
});
