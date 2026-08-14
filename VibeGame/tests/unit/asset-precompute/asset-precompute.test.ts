import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import { State } from '../../../src/core/ecs/state';
import {
  BodyType,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../../../src/plugins/physics/components';
import { setColliderMeshUrl } from '../../../src/plugins/physics/mesh-collider';
import { Transform } from '../../../src/plugins/transforms/components';
import {
  PrecomputeColliderSystem,
  resetPrecomputeForTests,
} from '../../../src/plugins/asset-precompute/systems';
import {
  getPrecomputeManifestState,
  getPrecomputeRetryAtMs,
  loadPrecomputeManifest,
  resetPrecomputeManifestForTests,
  resolvePrecompute,
} from '../../../src/plugins/asset-precompute/manifest';
import {
  resetResilientNetForTests,
  resilientNetConfig,
} from '../../../src/core/utils/resilient-net';
import {
  getGltfLocalAABB,
  seedGltfPrecomputedBounds,
} from '../../../src/plugins/gltf-xml/gltf-bounds-cache';
import { collectColliderObstacles } from '../../../src/plugins/navmesh/geometry';

const MANIFEST_PAYLOAD = {
  version: 1,
  rows: [
    {
      id: 'pine_dark',
      public_id: 'pine_dark',
      model: {
        url: '/assets/models/pine_dark_lod0.glb',
        lod: [
          '/assets/models/pine_dark_lod0.glb',
          '/assets/models/pine_dark_lod1.glb',
          '/assets/models/pine_dark_lod2.glb',
        ],
      },
      precompute: {
        version: 1,
        asset_id: 'pine_dark',
        category: 'vegetation',
        aabb: { min: [-1, 0, -1], max: [1, 5, 1] },
        collider: { shape: 'capsule', radius: 0.21, height: 5, base_y: 0 },
        source: 'trunk-slice',
        collectible_hint: { kind: 'wood' },
      },
    },
    {
      id: 'rock_mossy',
      public_id: 'rock_mossy',
      model: {
        url: '/assets/models/rock_mossy_lod0.glb',
        lod: ['/assets/models/rock_mossy_lod0.glb'],
      },
      precompute: {
        version: 1,
        asset_id: 'rock_mossy',
        category: 'rock',
        aabb: { min: [-0.6, 0, -0.8], max: [0.6, 1.2, 0.8] },
        collider: { shape: 'cylinder', radius: 0.6, height: 1.2, base_y: 0 },
        source: 'aabb',
        collectible_hint: { kind: 'stone' },
      },
    },
  ],
};

function mockFetchOk(): void {
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(MANIFEST_PAYLOAD), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })) as unknown as typeof fetch;
}

function mockFetch404(): void {
  globalThis.fetch = (async () =>
    new Response('nope', { status: 404 })) as unknown as typeof fetch;
}

describe('asset-precompute manifest', () => {
  beforeEach(() => {
    resetPrecomputeManifestForTests();
  });

  it('indexes rows with precompute blocks and resolves exact URL', async () => {
    mockFetchOk();
    const index = await loadPrecomputeManifest();
    expect(index).not.toBeNull();
    const entry = resolvePrecompute(index, '/assets/models/pine_dark_lod0.glb');
    expect(entry?.collider.shape).toBe('capsule');
    expect(entry?.collectible_hint?.kind).toBe('wood');
  });

  it('resolves by basename (path layout diferente: meshes/ vs models/)', async () => {
    mockFetchOk();
    const index = await loadPrecomputeManifest();
    const entry = resolvePrecompute(index, '/assets/meshes/pine_dark_lod0.glb');
    expect(entry?.collider.radius).toBe(0.21);
  });

  it('resolves by public_id with _lod0 stripped from the key', async () => {
    mockFetchOk();
    const index = await loadPrecomputeManifest();
    const entry = resolvePrecompute(index, 'rock_mossy_lod0.glb');
    expect(entry?.collider.shape).toBe('cylinder');
    expect(entry?.collider.radius).toBe(0.6);
  });

  it('resolves by bare public_id', async () => {
    mockFetchOk();
    const index = await loadPrecomputeManifest();
    const entry = resolvePrecompute(index, 'pine_dark');
    expect(entry?.collider.height).toBe(5);
  });

  it('returns undefined for unknown keys', async () => {
    mockFetchOk();
    const index = await loadPrecomputeManifest();
    expect(
      resolvePrecompute(index, '/assets/meshes/anvil_lod0.glb')
    ).toBeUndefined();
    expect(resolvePrecompute(null, 'pine_dark')).toBeUndefined();
  });

  it('marks absent on 404 and returns null', async () => {
    mockFetch404();
    const index = await loadPrecomputeManifest();
    expect(index).toBeNull();
    expect(getPrecomputeManifestState()).toBe('absent');
  });

  it('caches: second load reuses the same index', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return new Response(JSON.stringify(MANIFEST_PAYLOAD), { status: 200 });
    }) as unknown as typeof fetch;
    await loadPrecomputeManifest();
    await loadPrecomputeManifest();
    expect(fetches).toBe(1);
  });
});

describe('asset-precompute manifest resilience', () => {
  let realFetch: typeof fetch;

  beforeEach(() => {
    resetPrecomputeManifestForTests();
    resetResilientNetForTests();
    resilientNetConfig.retries = 0;
    resilientNetConfig.baseDelayMs = 1;
    realFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = realFetch;
    resetResilientNetForTests();
  });

  it('a transient 503 is NOT memorized as absent — it retries and recovers', async () => {
    let fetches = 0;
    globalThis.fetch = (async () => {
      fetches += 1;
      return fetches === 1
        ? new Response('busy', { status: 503 })
        : new Response(JSON.stringify(MANIFEST_PAYLOAD), { status: 200 });
    }) as unknown as typeof fetch;

    const first = await loadPrecomputeManifest();
    expect(first).toBeNull();
    // The defining regression: a flaky boot no longer poisons the session.
    expect(getPrecomputeManifestState()).toBe('idle');
    const retryAt = getPrecomputeRetryAtMs();
    expect(retryAt).toBeGreaterThan(Date.now());

    // During the backoff window calls are served from cache, no refetch.
    await loadPrecomputeManifest();
    expect(fetches).toBe(1);

    // Wait out the backoff, then the next kick must succeed.
    await new Promise<void>((resolve) => {
      const poll = () =>
        Date.now() >= retryAt ? resolve() : setTimeout(poll, 25);
      poll();
    });
    const second = await loadPrecomputeManifest();
    expect(second).not.toBeNull();
    expect(getPrecomputeManifestState()).toBe('loaded');
    expect(
      resolvePrecompute(second, '/assets/models/pine_dark_lod0.glb')?.collider
        .shape
    ).toBe('capsule');
    expect(fetches).toBe(2);
  });

  it('drops rows with invalid collider specs instead of indexing NaN', async () => {
    const hostile = {
      version: 1,
      rows: [
        {
          id: 'string_radius',
          model: { url: '/assets/models/string_radius.glb' },
          precompute: {
            collider: { shape: 'capsule', radius: 'big', height: 5, base_y: 0 },
          },
        },
        {
          id: 'negative_height',
          model: { url: '/assets/models/negative_height.glb' },
          precompute: {
            collider: { shape: 'cylinder', radius: 0.5, height: -2, base_y: 0 },
          },
        },
        {
          id: 'mystery_shape',
          model: { url: '/assets/models/mystery_shape.glb' },
          precompute: {
            collider: {
              shape: 'dodecahedron',
              radius: 1,
              height: 2,
              base_y: 0,
            },
          },
        },
        {
          id: 'pine_dark',
          model: { url: '/assets/models/pine_dark_lod0.glb' },
          precompute: {
            collider: { shape: 'capsule', radius: 0.21, height: 5, base_y: 0 },
          },
        },
      ],
    };
    globalThis.fetch = (async () =>
      new Response(JSON.stringify(hostile), {
        status: 200,
      })) as unknown as typeof fetch;

    const index = await loadPrecomputeManifest();
    expect(index).not.toBeNull();
    expect(
      resolvePrecompute(index, '/assets/models/string_radius.glb')
    ).toBeUndefined();
    expect(
      resolvePrecompute(index, '/assets/models/negative_height.glb')
    ).toBeUndefined();
    expect(
      resolvePrecompute(index, '/assets/models/mystery_shape.glb')
    ).toBeUndefined();
    expect(
      resolvePrecompute(index, '/assets/models/pine_dark_lod0.glb')?.collider
        .radius
    ).toBe(0.21);
  });
});

function runSystem(state: State): void {
  PrecomputeColliderSystem.update?.(state);
}

function makePrecomputeEntity(state: State, url: string): number {
  const eid = state.createEntity();
  state.addComponent(eid, Collider);
  state.addComponent(eid, Transform);
  Collider.shape[eid] = ColliderShape.Precompute;
  setColliderMeshUrl(state, eid, url);
  return eid;
}

describe('PrecomputeColliderSystem', () => {
  beforeEach(() => {
    resetPrecomputeForTests();
    mockFetchOk();
  });

  it('resolve cápsula do tronco com escala do spawner (radius × max(sx,sz), height × sy)', async () => {
    const state = new State();
    const eid = makePrecomputeEntity(
      state,
      '/assets/meshes/pine_dark_lod0.glb'
    );
    Transform.scaleX[eid] = 2;
    Transform.scaleY[eid] = 3;
    Transform.scaleZ[eid] = 1;
    runSystem(state);
    await loadPrecomputeManifest();
    runSystem(state);
    expect(Collider.shape[eid]).toBe(ColliderShape.Capsule);
    expect(Collider.radius[eid]).toBeCloseTo(0.21 * 2, 5);
    expect(Collider.height[eid]).toBeCloseTo(5 * 3, 5);
    expect(Collider.posOffsetY[eid]).toBeCloseTo((0 + 5 / 2) * 3, 5);
  });

  it('resolve cilindro para pedras', async () => {
    const state = new State();
    const eid = makePrecomputeEntity(state, 'rock_mossy_lod0.glb');
    runSystem(state);
    await loadPrecomputeManifest();
    runSystem(state);
    expect(Collider.shape[eid]).toBe(ColliderShape.Cylinder);
    expect(Collider.radius[eid]).toBeCloseTo(0.6, 5);
    expect(Collider.height[eid]).toBeCloseTo(1.2, 5);
  });

  it('semear o bounds cache com o AABB do manifest', async () => {
    const state = new State();
    makePrecomputeEntity(state, '/assets/meshes/pine_dark_lod0.glb');
    runSystem(state);
    await loadPrecomputeManifest();
    runSystem(state);
    const bounds = getGltfLocalAABB('/assets/meshes/pine_dark_lod0.glb');
    expect(bounds?.minY).toBe(0);
    expect(bounds?.maxY).toBe(5);
    expect(bounds?.minX).toBe(-1);
  });

  it('mantém o marker enquanto o manifest carrega (physics init salta Precompute)', async () => {
    let resolveFetch: (r: Response) => void = () => {};
    globalThis.fetch = (() =>
      new Promise<Response>((res) => {
        resolveFetch = res;
      })) as unknown as typeof fetch;
    const state = new State();
    const eid = makePrecomputeEntity(
      state,
      '/assets/meshes/pine_dark_lod0.glb'
    );
    runSystem(state);
    runSystem(state);
    expect(Collider.shape[eid]).toBe(ColliderShape.Precompute);
    resolveFetch(
      new Response(JSON.stringify(MANIFEST_PAYLOAD), { status: 200 })
    );
    await loadPrecomputeManifest();
    runSystem(state);
    expect(Collider.shape[eid]).toBe(ColliderShape.Capsule);
  });

  it('fallback AABB-fit (bounds do cache) quando o manifest não tem a URL', async () => {
    const state = new State();
    const url = '/assets/meshes/anvil_lod0.glb';
    seedGltfPrecomputedBounds(url, {
      min: [-0.5, 0, -0.5],
      max: [0.5, 2, 0.5],
    });
    const eid = makePrecomputeEntity(state, url);
    runSystem(state);
    await loadPrecomputeManifest();
    runSystem(state);
    expect(Collider.shape[eid]).toBe(ColliderShape.Capsule);
    expect(Collider.radius[eid]).toBeCloseTo(0.5, 5); // min(1,1)/2
    expect(Collider.height[eid]).toBeCloseTo(1.0, 5); // 2 - 2·0.5
    expect(Collider.posOffsetY[eid]).toBeCloseTo(1.0, 5);
  });

  it('fallback default quando não há manifest nem bounds', async () => {
    mockFetch404();
    const state = new State();
    const eid = makePrecomputeEntity(state, '/assets/meshes/mystery_lod0.glb');
    Transform.scaleY[eid] = 2;
    runSystem(state);
    await loadPrecomputeManifest();
    runSystem(state);
    expect(Collider.shape[eid]).toBe(ColliderShape.Capsule);
    expect(Collider.radius[eid]).toBeCloseTo(0.3, 5);
    expect(Collider.height[eid]).toBeCloseTo(1.5 * 2, 5);
  });
});

describe('navmesh carve procedural (cápsula/cilindro)', () => {
  function makeObstacle(
    state: State,
    shape: ColliderShape,
    radius: number,
    height: number,
    posOffsetY: number,
    opts: { dynamic?: boolean; sensor?: boolean } = {}
  ): number {
    const eid = state.createEntity();
    state.addComponent(eid, Collider);
    state.addComponent(eid, Rigidbody);
    state.addComponent(eid, Transform);
    Rigidbody.type[eid] = opts.dynamic ? BodyType.Dynamic : BodyType.Fixed;
    Collider.shape[eid] = shape;
    Collider.radius[eid] = radius;
    Collider.height[eid] = height;
    Collider.posOffsetY[eid] = posOffsetY;
    if (opts.sensor) Collider.isSensor[eid] = 1;
    return eid;
  }

  it('cápsula corta um prisma da altura total (height + 2·radius) com raio do tronco', () => {
    const state = new State();
    makeObstacle(state, ColliderShape.Capsule, 0.4, 2.0, 1.4);
    const geo = collectColliderObstacles(state, 120);
    expect(geo).not.toBeNull();
    const pos = geo!.positions;
    expect(pos.length).toBeGreaterThan(0);
    let minY = Infinity;
    let maxY = -Infinity;
    let maxR = 0;
    for (let i = 0; i < pos.length; i += 3) {
      minY = Math.min(minY, pos[i + 1]);
      maxY = Math.max(maxY, pos[i + 1]);
      maxR = Math.max(maxR, Math.hypot(pos[i], pos[i + 2]));
    }
    expect(minY).toBeCloseTo(0, 2);
    expect(maxY).toBeCloseTo(2.8, 2);
    expect(maxR).toBeCloseTo(0.4, 2);
  });

  it('cilindro corta com altura exata (sem caps arredondadas)', () => {
    const state = new State();
    makeObstacle(state, ColliderShape.Cylinder, 0.6, 1.2, 0.6);
    const geo = collectColliderObstacles(state, 120);
    expect(geo).not.toBeNull();
    const pos = geo!.positions;
    let minY = Infinity;
    let maxY = -Infinity;
    for (let i = 0; i < pos.length; i += 3) {
      minY = Math.min(minY, pos[i + 1]);
      maxY = Math.max(maxY, pos[i + 1]);
    }
    expect(minY).toBeCloseTo(0, 2);
    expect(maxY).toBeCloseTo(1.2, 2);
  });

  it('cápsula dinâmica não corta o navmesh (só fixed)', () => {
    const state = new State();
    makeObstacle(state, ColliderShape.Capsule, 0.4, 2.0, 1.4, {
      dynamic: true,
    });
    expect(collectColliderObstacles(state, 120)).toBeNull();
  });

  it('cápsula sensor não corta o navmesh', () => {
    const state = new State();
    makeObstacle(state, ColliderShape.Capsule, 0.4, 2.0, 1.4, { sensor: true });
    expect(collectColliderObstacles(state, 120)).toBeNull();
  });

  it('fora do bounds não corta', () => {
    const state = new State();
    const eid = makeObstacle(state, ColliderShape.Cylinder, 0.6, 1.2, 0.6);
    Rigidbody.posX[eid] = 500;
    expect(collectColliderObstacles(state, 120)).toBeNull();
  });
});

describe('collider shape plumbing', () => {
  it('ColliderShape enum expõe Cylinder e Precompute (não chegam ao Rapier)', () => {
    expect(ColliderShape.Cylinder).toBe(5);
    expect(ColliderShape.Precompute).toBe(6);
    expect(ColliderShape.Capsule).toBe(2);
  });
});
