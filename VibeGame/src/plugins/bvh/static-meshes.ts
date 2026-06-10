import * as THREE from 'three';
import { defineQuery, type State } from '../../core';
import { GltfLod } from '../gltf-xml/components';
import { forEachGltfRootGroup } from '../gltf-xml/group-registry';
import { BodyType, Rigidbody } from '../physics/components';
import { WorldTransform } from '../transforms';
import { registerBvhMesh, unregisterBvhForEntity } from './utils';

const rigidbodyQuery = defineQuery([Rigidbody, WorldTransform]);

/** Entity → GLTF root group it was baked from. A different group for the same
 * id means the id was recycled (or the GLTF reloaded) → rebuild. */
const built = new WeakMap<State, Map<number, THREE.Object3D>>();

function getBuilt(state: State): Map<number, THREE.Object3D> {
  let m = built.get(state);
  if (!m) {
    m = new Map();
    built.set(state, m);
  }
  return m;
}

const _mat = new THREE.Matrix4();
const _instMat = new THREE.Matrix4();
const _v = new THREE.Vector3();

/**
 * Bake all triangles below `root` into a single indexed BufferGeometry with
 * vertices already in world space (multiplied by the local matrix chain).
 * Indices are preserved (no triangle-soup expansion) and no normal attribute
 * is generated — the BVH raycast derives face normals geometrically and the
 * mesh is never rendered.
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

  let vertCount = 0;
  let indexCount = 0;
  for (const m of meshes) {
    const g = m.geometry;
    const instances = (m as THREE.InstancedMesh).isInstancedMesh
      ? (m as THREE.InstancedMesh).count
      : 1;
    vertCount += g.attributes.position.count * instances;
    indexCount +=
      (g.index ? g.index.count : g.attributes.position.count) * instances;
  }
  if (vertCount === 0 || indexCount === 0) return null;

  const positions = new Float32Array(vertCount * 3);
  const indices = new Uint32Array(indexCount);

  let vOffset = 0;
  let iOffset = 0;

  for (const m of meshes) {
    const g = m.geometry;
    const posAttr = g.attributes.position as THREE.BufferAttribute;
    const index = g.index;
    const instanced = (m as THREE.InstancedMesh).isInstancedMesh
      ? (m as THREE.InstancedMesh)
      : null;
    const reps = instanced ? instanced.count : 1;

    for (let r = 0; r < reps; r++) {
      if (instanced) {
        instanced.getMatrixAt(r, _instMat);
        _mat.multiplyMatrices(m.matrixWorld, _instMat);
      } else {
        _mat.copy(m.matrixWorld);
      }

      for (let i = 0; i < posAttr.count; i++) {
        _v.fromBufferAttribute(posAttr, i).applyMatrix4(_mat);
        const o = (vOffset + i) * 3;
        positions[o] = _v.x;
        positions[o + 1] = _v.y;
        positions[o + 2] = _v.z;
      }

      if (index) {
        for (let i = 0; i < index.count; i++) {
          indices[iOffset++] = vOffset + index.getX(i);
        }
      } else {
        for (let i = 0; i < posAttr.count; i++) {
          indices[iOffset++] = vOffset + i;
        }
      }
      vOffset += posAttr.count;
    }
  }

  const out = new THREE.BufferGeometry();
  out.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  out.setIndex(new THREE.BufferAttribute(indices, 1));
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
  const map = getBuilt(state);
  let added = 0;
  let removed = 0;

  forEachGltfRootGroup(state, (entity, group) => {
    const prevGroup = map.get(entity);
    if (prevGroup === group) return;
    if (prevGroup) {
      // Same id, different root: recycled entity id or reloaded GLTF.
      unregisterBvhForEntity(state, entity);
      map.delete(entity);
      removed++;
    }

    let shouldInclude = false;
    if (state.hasComponent(entity, Rigidbody)) {
      shouldInclude = Rigidbody.type[entity] === BodyType.Fixed;
    } else {
      // GLTF without rigidbody → treat as static decorative geometry.
      shouldInclude = true;
    }
    if (!shouldInclude) return;

    // LOD roots keep every level as a sibling child with visibility toggled
    // per frame; bake only LOD0 or each level's triangles would pile up.
    let bakeRoot: THREE.Object3D = group;
    if (state.hasComponent(entity, GltfLod) && group.children.length >= 2) {
      bakeRoot = group.children[0];
    }

    const geometry = bakeObject3DGeometry(bakeRoot);
    if (!geometry) return;

    registerBvhMesh(state, `gltf:${entity}`, geometry, {
      entity,
      layer: 0x0002,
      source: group,
    });
    map.set(entity, group);
    added++;
  });

  // Cleanup destroyed entities.
  for (const entity of [...map.keys()]) {
    if (!state.exists(entity)) {
      unregisterBvhForEntity(state, entity);
      map.delete(entity);
      removed++;
    }
  }

  // Untrack Fixed → Dynamic flips on existing entities.
  for (const entity of rigidbodyQuery(state.world)) {
    if (!map.has(entity)) continue;
    if (Rigidbody.type[entity] !== BodyType.Fixed) {
      unregisterBvhForEntity(state, entity);
      map.delete(entity);
      removed++;
    }
  }

  return { added, removed, total: map.size };
}
