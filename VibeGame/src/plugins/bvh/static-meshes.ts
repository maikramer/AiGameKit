import * as THREE from 'three';
import { defineQuery, type State } from '../../core';
import { forEachGltfRootGroup } from '../gltf-xml/group-registry';
import { BodyType, Rigidbody } from '../physics/components';
import { WorldTransform } from '../transforms';
import { registerBvhMesh, unregisterBvhForEntity } from './utils';

const rigidbodyQuery = defineQuery([Rigidbody, WorldTransform]);

const built = new WeakMap<State, Set<number>>();

function getBuilt(state: State): Set<number> {
  let s = built.get(state);
  if (!s) {
    s = new Set();
    built.set(state, s);
  }
  return s;
}

const _mat = new THREE.Matrix4();

/**
 * Bake all triangles below `root` into a single BufferGeometry with vertices
 * already in world space (multiplied by the local matrix chain).
 */
function bakeObject3DGeometry(
  root: THREE.Object3D
): THREE.BufferGeometry | null {
  const meshes: THREE.Mesh[] = [];
  root.traverse((obj) => {
    const m = obj as THREE.Mesh;
    if (m.isMesh && m.geometry) meshes.push(m);
  });
  if (meshes.length === 0) return null;

  root.updateWorldMatrix(true, true);

  let totalTris = 0;
  for (const m of meshes) {
    const g = m.geometry;
    const triCount = g.index
      ? g.index.count / 3
      : g.attributes.position.count / 3;
    totalTris += triCount;
  }
  if (totalTris === 0) return null;

  const positions = new Float32Array(totalTris * 9);
  let offset = 0;

  for (const m of meshes) {
    const g = m.geometry;
    const posAttr = g.attributes.position as THREE.BufferAttribute;
    const index = g.index;
    _mat.copy(m.matrixWorld);

    const _v = new THREE.Vector3();
    const triCount = index ? index.count / 3 : posAttr.count / 3;
    for (let t = 0; t < triCount; t++) {
      for (let k = 0; k < 3; k++) {
        const vi = index ? index.getX(t * 3 + k) : t * 3 + k;
        _v.fromBufferAttribute(posAttr, vi).applyMatrix4(_mat);
        positions[offset++] = _v.x;
        positions[offset++] = _v.y;
        positions[offset++] = _v.z;
      }
    }
  }

  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  out.computeVertexNormals();
  return out;
}

/**
 * Register every entity that is a static GLTF (`Rigidbody.type === Fixed` and
 * has a GLTF root) into the BVH. We bake the world-space geometry once.
 *
 * Returns counters useful for tests and debug.
 */
export function syncStaticMeshBvh(state: State): {
  added: number;
  removed: number;
  total: number;
} {
  const set = getBuilt(state);
  let added = 0;
  let removed = 0;

  forEachGltfRootGroup(state, (entity, group) => {
    if (set.has(entity)) return;

    let shouldInclude = false;
    if (state.hasComponent(entity, Rigidbody)) {
      shouldInclude = Rigidbody.type[entity] === BodyType.Fixed;
    } else {
      // GLTF without rigidbody → treat as static decorative geometry.
      shouldInclude = true;
    }
    if (!shouldInclude) return;

    const geometry = bakeObject3DGeometry(group);
    if (!geometry) return;

    registerBvhMesh(state, `gltf:${entity}`, geometry, {
      entity,
      layer: 0x0002,
      source: group,
    });
    set.add(entity);
    added++;
  });

  // Cleanup destroyed entities.
  for (const entity of [...set]) {
    if (!state.exists(entity)) {
      unregisterBvhForEntity(state, entity);
      set.delete(entity);
      removed++;
    }
  }

  // Untrack Fixed → Dynamic flips on existing entities.
  for (const entity of rigidbodyQuery(state.world)) {
    if (!set.has(entity)) continue;
    if (Rigidbody.type[entity] !== BodyType.Fixed) {
      unregisterBvhForEntity(state, entity);
      set.delete(entity);
      removed++;
    }
  }

  return { added, removed, total: set.size };
}
