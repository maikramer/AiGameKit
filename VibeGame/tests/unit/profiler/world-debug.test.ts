import { describe, expect, it } from 'bun:test';
import { Parent, State, Transform } from 'vibegame';
import { setGltfUrl } from '../../../src/plugins/gltf-xml/context';
import { Health } from '../../../src/plugins/combat/components';
import { TerrainSpawned } from '../../../src/plugins/spawner/components';
import {
  assetStem,
  getWorldDebugSnapshot,
  renderWorldTab,
  resolveEntityLabel,
  type WorldDebugSnapshot,
} from '../../../src/plugins/profiler/world-debug';

describe('assetStem', () => {
  it('strips path and extension', () => {
    expect(assetStem('/assets/meshes/cactus_lod0.glb')).toBe('cactus_lod0');
    expect(assetStem('dead_tree_lod1.glb')).toBe('dead_tree_lod1');
  });
});

describe('resolveEntityLabel', () => {
  it('prefers entity name, then gltf stem', () => {
    const state = new State();
    const named = state.createEntity();
    state.addComponent(named, Transform);
    state.setEntityName('desert-sun-obelisk', named);
    expect(resolveEntityLabel(state, named).name).toBe('desert-sun-obelisk');
    expect(resolveEntityLabel(state, named).source).toBe('name');

    const anon = state.createEntity();
    state.addComponent(anon, Transform);
    setGltfUrl(state, anon, '/assets/meshes/cactus_lod0.glb');
    expect(resolveEntityLabel(state, anon)).toEqual({
      name: 'cactus_lod0',
      source: 'gltf',
    });
  });

  it('resolves gltf url on child GLTFLoader', () => {
    const state = new State();
    const root = state.createEntity();
    const child = state.createEntity();
    state.addComponent(root, Transform);
    state.addComponent(child, Transform);
    state.addComponent(child, Parent, { entity: root });
    setGltfUrl(state, child, '/assets/meshes/tree_pine_lod0.glb');
    expect(resolveEntityLabel(state, root, [child])).toEqual({
      name: 'tree_pine_lod0',
      source: 'gltf',
    });
  });
});

describe('getWorldDebugSnapshot', () => {
  it('labels nearby from gltf and fills detail for JSON', () => {
    const state = new State();
    const player = state.createEntity();
    state.addComponent(player, Transform, {
      posX: 0,
      posY: 1,
      posZ: 0,
    });
    state.setEntityName('hero', player);
    // PlayerController required for player resolve — skip; nearby uses origin 0

    const tree = state.createEntity();
    state.addComponent(tree, Transform, {
      posX: 3,
      posY: 1,
      posZ: 0,
    });
    state.addComponent(tree, Health);
    Health.current[tree] = 10;
    Health.max[tree] = 10;
    state.addComponent(tree, TerrainSpawned);
    TerrainSpawned.yOffset[tree] = 0.5;
    setGltfUrl(state, tree, '/assets/meshes/cactus_lod0.glb');

    const snap = getWorldDebugSnapshot(state, {
      nearbyRadius: 30,
      nearbyLimit: 24,
    });
    expect(snap.nearbyInRadius).toBeGreaterThanOrEqual(1);
    const row = snap.nearby.find((n) => n.eid === tree);
    expect(row).toBeDefined();
    expect(row!.name).toBe('cactus_lod0');
    expect(row!.detail.labelSource).toBe('gltf');
    expect(row!.detail.gltfUrl).toContain('cactus_lod0');
    expect(row!.detail.terrainSpawned?.yOffset).toBeCloseTo(0.5);
    expect(row!.detail.health?.current).toBe(10);
    expect(row!.tags).toContain('health');
    expect(row!.tags).toContain('terrain-spawned');
  });
});

describe('renderWorldTab', () => {
  it('formats player, camera, and nearby rows', () => {
    const snap: WorldDebugSnapshot = {
      t: 0,
      frame: 42,
      nearbyRadius: 30,
      nearbyLimit: 24,
      nearbyInRadius: 1,
      origin: { x: 1, y: 2, z: 3 },
      entityCount: 10,
      player: {
        eid: 1,
        name: 'hero',
        pos: { x: 1, y: 2, z: 3 },
        worldPos: { x: 1, y: 2, z: 3 },
        eulerYDeg: 90,
        grounded: true,
        terrainY: 1.5,
        groundY: 1.5,
        deltaGroundY: 0.5,
        densityBoost: 0,
        vel: { x: 0, y: 0, z: 1 },
      },
      camera: {
        eid: 2,
        name: 'cam',
        pos: { x: 0, y: 5, z: -8 },
        worldPos: { x: 0, y: 5, z: -8 },
        fov: 60,
        near: 0.1,
        far: 500,
        threePos: { x: 0, y: 5, z: -8 },
        tpc: {
          eid: 3,
          target: 1,
          distance: 6,
          height: 2,
          yawDeg: 45,
          pitchDeg: -15,
          follow: { x: 1, y: 2, z: 3 },
        },
      },
      nearby: [
        {
          eid: 9,
          name: 'goblin_lod0',
          pos: { x: 4, y: 2, z: 3 },
          dist: 3,
          tags: ['health', 'ai'],
          detail: {
            labelSource: 'gltf',
            worldPos: { x: 4, y: 2, z: 3 },
            eulerDeg: { x: 0, y: 0, z: 0 },
            scale: { x: 1, y: 1, z: 1 },
            terrainY: 2,
            groundY: 2,
            surfaceY: 2,
            deltaGroundY: 0,
            densityBoost: 0,
            parent: null,
            children: [],
            gltfUrl: '/assets/meshes/goblin_lod0.glb',
            lodUrls: null,
            gltfPending: false,
            gltfLodLevel: 0,
            script: 'creature.ts',
            monoReady: true,
            health: { current: 40, max: 40 },
            faction: 'enemy',
            ai: {
              mode: 'idle',
              modeId: 0,
              target: 0,
              cooldown: 0,
              leash: 0,
            },
            destructible: null,
            terrainSpawned: {
              yOffset: 0.02,
              alignToTerrain: false,
              aabbPending: false,
              surfaceEpsilon: 0.75,
              scaleY: 1,
              normalY: 1,
              aabbPendingUrl: null,
            },
            distanceCull: { maxDistance: 100, culled: false },
            navAgent: true,
            rigidbody: null,
            collider: null,
            resource: null,
            variation: null,
            components: ['health', 'ai', 'terrain-spawned'],
          },
        },
      ],
    };
    const text = renderWorldTab(snap);
    expect(text).toContain('hero');
    expect(text).toContain('grounded=yes');
    expect(text).toContain('fov=60.0');
    expect(text).toContain('goblin_lod0');
    expect(text).toContain('[health,ai]');
    expect(text).toContain('⟨gltf⟩');
    expect(text).toContain('worldSnapshot()');
    expect(text).not.toContain('detail.health');
  });
});
