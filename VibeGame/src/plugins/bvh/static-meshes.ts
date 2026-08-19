import * as THREE from 'three';
import { defineQuery, defineQueryLive, type State } from '../../core';
import { GltfLod, GltfPending } from '../gltf-xml/components';
import { forEachGltfRootGroup } from '../gltf-xml/group-registry';
import { getLodChild, lodChildCount } from '../../extras/gltf-lod-parking';
import { BodyType, Rigidbody } from '../physics/components';
import { WorldTransform } from '../transforms';
import { registerBvhMesh, unregisterBvhForEntity } from './utils';

const rigidbodyQuery = defineQuery([Rigidbody, WorldTransform]);
// Read-only count over every dressed prop in the world — iterate the live dense
// set rather than snapshotting ~91k ids into a fresh array once per frame.
const gltfPendingQuery = defineQueryLive([GltfPending]);

/** Entity → GLTF root group it was baked from. A different group for the same
 * id means the id was recycled (or the GLTF reloaded) → rebuild. */
const built = new WeakMap<State, Map<number, THREE.Object3D>>();
/** Previous unloaded-GLTF count — when it drops to 0 we force one more bake pass. */
const prevPendingUnload = new WeakMap<State, number>();

function getBuilt(state: State): Map<number, THREE.Object3D> {
  let m = built.get(state);
  if (!m) {
    m = new Map();
    built.set(state, m);
  }
  return m;
}

function countUnloadedGltfs(state: State): number {
  let n = 0;
  for (const eid of gltfPendingQuery(state.world)) {
    if (GltfPending.loaded[eid] !== 1) n++;
  }
  return n;
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

  const pendingUnload = countUnloadedGltfs(state);
  const prevPending = prevPendingUnload.get(state) ?? -1;
  // Full GLTF walk is only needed while assets are still loading, right after
  // the last pending finishes, or before anything has been baked yet.
  const needsFullScan =
    pendingUnload > 0 || prevPending !== 0 || map.size === 0;
  prevPendingUnload.set(state, pendingUnload);

  if (needsFullScan) {
    forEachGltfRootGroup(state, (entity, group) => {
      const prevGroup = map.get(entity);
      if (prevGroup === group) return;
      if (prevGroup) {
        // Same id, different root: recycled entity id or reloaded GLTF.
        unregisterBvhForEntity(state, entity);
        map.delete(entity);
        removed++;
      }

      const shouldInclude = state.hasComponent(entity, Rigidbody)
        ? Rigidbody.type[entity] === BodyType.Fixed
        : true;
      if (!shouldInclude) return;

      // A LOD root owns one level per distance band and only keeps the active
      // one attached; bake LOD0 specifically, or the collision surface would
      // follow whichever level happens to be on screen.
      let bakeRoot: THREE.Object3D = group;
      let parkedLod0: THREE.Object3D | undefined;
      if (state.hasComponent(entity, GltfLod) && lodChildCount(group) >= 2) {
        const lod0 = getLodChild(group, 0);
        if (lod0) {
          bakeRoot = lod0;
          // The bake bakes the *world* matrix chain. A LOD0 parked off-graph
          // (prop spawned already far away) would bake in local space and put
          // the collision mesh at the world origin, so re-attach it for the
          // duration of the bake and hand the root back to the LOD system.
          if (lod0.parent !== group) {
            parkedLod0 = lod0;
            group.add(lod0);
            lod0.visible = false;
            group.updateMatrixWorld(true);
          }
        }
      }

      const geometry = bakeObject3DGeometry(bakeRoot);
      if (parkedLod0) {
        group.remove(parkedLod0);
        parkedLod0.visible = true;
      }
      if (!geometry) return;

      registerBvhMesh(state, `gltf:${entity}`, geometry, {
        entity,
        layer: 0x0002,
        source: group,
      });
      map.set(entity, group);
      added++;
    });
  }

  // Cleanup destroyed entities.
  map.forEach((_value, entity) => {
    if (!state.exists(entity)) {
      unregisterBvhForEntity(state, entity);
      map.delete(entity);
      removed++;
    }
  });

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
