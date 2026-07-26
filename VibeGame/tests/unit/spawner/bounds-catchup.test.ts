import { describe, expect, it, beforeEach } from 'bun:test';
import * as THREE from 'three';
import { State, Transform, getGltfLocalYBounds } from 'vibegame';
import { registerGltfLocalYBounds } from '../../../src/plugins/gltf-xml';
import {
  TerrainSpawnBoundsCatchUpSystem,
  TerrainSpawned,
} from '../../../src/plugins/spawner';
import {
  getAabbPendingUrls,
  setAabbPendingUrl,
} from '../../../src/plugins/spawner/bounds-context';

const URL = '/assets/meshes/tree_oak_lod0.glb';

/** Build a tiny root whose local AABB has min.y at `baseY` (e.g. 1.2). */
function makeRoot(baseY: number, height = 2): THREE.Object3D {
  const geo = new THREE.BufferGeometry();
  geo.setAttribute(
    'position',
    new THREE.BufferAttribute(
      new Float32Array([0, baseY, 0, 1, baseY, 0, 0, baseY + height, 0]),
      3
    )
  );
  geo.computeBoundingBox();
  const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial());
  mesh.updateMatrixWorld(true);
  return mesh;
}

function makePendingProp(
  state: State,
  opts: {
    spawnY: number;
    surfaceY: number;
    scaleY: number;
    normalY: number;
    url: string;
  }
): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform, {
    posX: 10,
    posY: opts.spawnY,
    posZ: 20,
  });
  state.addComponent(eid, TerrainSpawned);
  TerrainSpawned.yOffset[eid] = opts.spawnY - opts.surfaceY;
  TerrainSpawned.aabbPending[eid] = 1;
  TerrainSpawned.scaleY[eid] = opts.scaleY;
  TerrainSpawned.normalY[eid] = opts.normalY;
  setAabbPendingUrl(state, eid, opts.url);
  return eid;
}

function tick(state: State): void {
  TerrainSpawnBoundsCatchUpSystem.update!(state);
}

describe('TerrainSpawnBoundsCatchUpSystem', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerComponent('transform', Transform);
    state.registerComponent('terrainSpawned', TerrainSpawned);
  });

  it('no-op when no entities are pending AABB bounds', () => {
    // No entities at all → the query returns nothing and urls stays empty.
    expect(() => tick(state)).not.toThrow();
  });

  it('does not move the prop while the GLB bounds are still loading', () => {
    // Bounds not registered yet (prefetch in flight).
    expect(getGltfLocalYBounds(URL)).toBeNull();

    const eid = makePendingProp(state, {
      spawnY: 5, // where the spawner placed it (only baseYOffset, no AABB lift)
      surfaceY: 5,
      scaleY: 2,
      normalY: 1,
      url: URL,
    });
    const before = Transform.posY[eid];

    tick(state);

    expect(Transform.posY[eid]).toBe(before);
    expect(TerrainSpawned.aabbPending[eid]).toBe(1);
    // URL still tracked for the next frame.
    expect(getAabbPendingUrls(state).has(eid)).toBe(true);
  });

  it('applies the missing AABB lift once the bounds arrive', () => {
    // Spawn without bounds (lift skipped): model origin is above its feet, so
    // the prop floats at spawnY = surfaceY.
    const surfaceY = 12;
    const eid = makePendingProp(state, {
      spawnY: surfaceY,
      surfaceY,
      scaleY: 2,
      normalY: 1, // not aligning to terrain → lift applied purely along Y
      url: URL,
    });

    // Bounds arrive: the GLB's local min.y is 1.2 (origin above the feet).
    registerGltfLocalYBounds(URL, makeRoot(1.2));
    const bounds = getGltfLocalYBounds(URL)!;
    expect(bounds.minY).toBeCloseTo(1.2, 5);

    tick(state);

    // lift = normalY * (-minY * scaleY) = 1 * (-1.2 * 2) = -2.4
    const expected = surfaceY + 1 * (-bounds.minY * 2);
    expect(Transform.posY[eid]).toBeCloseTo(expected, 5);
    expect(TerrainSpawned.yOffset[eid]).toBeCloseTo(expected - surfaceY, 5);
    expect(TerrainSpawned.aabbPending[eid]).toBe(0);
    expect(getAabbPendingUrls(state).has(eid)).toBe(false);
  });

  it('lift follows the stored normalY when aligning to terrain', () => {
    const surfaceY = 8;
    const eid = makePendingProp(state, {
      spawnY: surfaceY,
      surfaceY,
      scaleY: 1.5,
      normalY: 0.9, // slope-tilted normal: only 90% of the lift lands in Y
      url: URL,
    });

    registerGltfLocalYBounds(URL, makeRoot(1.0));

    tick(state);

    // lift_y = 0.9 * (-1.0 * 1.5) = -1.35
    expect(Transform.posY[eid]).toBeCloseTo(surfaceY - 1.35, 5);
    expect(TerrainSpawned.aabbPending[eid]).toBe(0);
  });

  it('latches: a caught-up entity is not touched again on later frames', () => {
    const surfaceY = 3;
    const eid = makePendingProp(state, {
      spawnY: surfaceY,
      surfaceY,
      scaleY: 1,
      normalY: 1,
      url: URL,
    });

    registerGltfLocalYBounds(URL, makeRoot(0.5));
    tick(state);
    const caught = Transform.posY[eid];
    expect(TerrainSpawned.aabbPending[eid]).toBe(0);

    // A second tick must not stack another lift on top.
    tick(state);
    expect(Transform.posY[eid]).toBe(caught);
  });

  it('drops orphaned URL entries whose entity lost TerrainSpawned', () => {
    const eid = makePendingProp(state, {
      spawnY: 5,
      surfaceY: 5,
      scaleY: 1,
      normalY: 1,
      url: URL,
    });
    expect(getAabbPendingUrls(state).has(eid)).toBe(true);

    // Bounds never arrive; entity is removed from the spawned query (e.g. its
    // TerrainSpawned component was dropped on teardown).
    state.removeComponent(eid, TerrainSpawned);

    tick(state);

    expect(getAabbPendingUrls(state).has(eid)).toBe(false);
  });
});
