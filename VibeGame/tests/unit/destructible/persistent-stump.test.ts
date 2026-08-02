import { describe, expect, it } from 'bun:test';
import * as THREE from 'three';
import { State } from '../../../src/core/ecs/state';
import {
  BodyType,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../../../src/plugins/physics/components';
import { Transform } from '../../../src/plugins/transforms/components';
import { GltfPending } from '../../../src/plugins/gltf-xml/components';
import { getGltfRootGroup } from '../../../src/plugins/gltf-xml/group-registry';
import { spawnPersistentStump } from '../../../src/plugins/destructible/fx';

function makeSplitSource(): THREE.Group {
  const root = new THREE.Group();
  const stump = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1));
  stump.name = 'Stump';
  stump.position.y = 0.3;
  const top = new THREE.Mesh(new THREE.BoxGeometry(1, 3, 1));
  top.name = 'Top';
  top.position.y = 2.5;
  root.add(stump, top);
  return root;
}

describe('spawnPersistentStump', () => {
  it('cria entidade estática com colisor cápsula copiado + GltfPending pronto', () => {
    const state = new State();
    const source = makeSplitSource();
    const eid = spawnPersistentStump(
      state,
      {
        x: 11.1,
        y: 36.4,
        z: 60.8,
        rotX: 0,
        rotY: 0.5,
        rotZ: 0,
        rotW: 1,
        sx: 1.4,
        sy: 1.4,
        sz: 1.4,
        radius: 1.29,
        height: 0.82,
        posOffsetY: 0.41,
      },
      source
    );
    expect(eid).not.toBeNull();
    expect(state.hasComponent(eid!, Collider)).toBe(true);
    expect(Collider.shape[eid!]).toBe(ColliderShape.Capsule);
    expect(Collider.radius[eid!]).toBeCloseTo(1.29, 5);
    expect(Collider.height[eid!]).toBeCloseTo(0.82, 5);
    expect(Collider.posOffsetY[eid!]).toBeCloseTo(0.41, 5);
    expect(state.hasComponent(eid!, Rigidbody)).toBe(true);
    expect(Rigidbody.type[eid!]).toBe(BodyType.Fixed);
    expect(Rigidbody.posX[eid!]).toBeCloseTo(11.1, 5);
    expect(state.hasComponent(eid!, Transform)).toBe(true);
    expect(Transform.scaleY[eid!]).toBeCloseTo(1.4, 5);
    expect(state.hasComponent(eid!, GltfPending)).toBe(true);
    expect(GltfPending.loaded[eid!]).toBe(1);
  });

  it('regista o grupo visual com o clone local da peça Stump', () => {
    const state = new State();
    const source = makeSplitSource();
    const eid = spawnPersistentStump(
      state,
      {
        x: 0,
        y: 0,
        z: 0,
        rotX: 0,
        rotY: 0,
        rotZ: 0,
        rotW: 1,
        sx: 1,
        sy: 1,
        sz: 1,
        radius: 0.3,
        height: 0.6,
        posOffsetY: 0.3,
      },
      source
    );
    const group = getGltfRootGroup(state, eid!);
    expect(group).toBeDefined();
    const names = new Set<string>();
    group!.traverse((o) => names.add(o.name));
    expect(names.has('Stump')).toBe(true);
    expect(names.has('Top')).toBe(false);
  });

  it('devolve null sem peças Stump/Top (fallback sem stump persistente)', () => {
    const state = new State();
    const source = new THREE.Group();
    source.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1)));
    const eid = spawnPersistentStump(
      state,
      {
        x: 0,
        y: 0,
        z: 0,
        rotX: 0,
        rotY: 0,
        rotZ: 0,
        rotW: 1,
        sx: 1,
        sy: 1,
        sz: 1,
        radius: 0.3,
        height: 0.6,
        posOffsetY: 0.3,
      },
      source
    );
    expect(eid).toBeNull();
  });
});
