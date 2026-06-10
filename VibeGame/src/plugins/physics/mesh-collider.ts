import * as THREE from 'three';
import type { State } from '../../core';

/**
 * Collision-mesh colliders (`collider="shape: trimesh; mesh-url: …"`).
 *
 * Loads a GLB and extracts node-transformed POSITION + index data for
 * Rapier's trimesh/convexHull descriptors. Parses the GLB manually (JSON +
 * BIN chunks) instead of going through THREE.GLTFLoader so it stays usable
 * headless and never touches the scene graph — collision GLBs are tiny,
 * untextured low-poly hulls.
 */

export interface ColliderMeshData {
  /** xyz triplets, world-space within the GLB (node transforms applied). */
  vertices: Float32Array;
  indices: Uint32Array;
}

export enum MeshAnchor {
  None = 0,
  /** Recenter so the AABB base center sits at the entity origin. */
  Base = 1,
}

const urlByState = new WeakMap<State, Map<number, string>>();

type CacheEntry =
  | { status: 'loading' }
  | { status: 'ready'; data: ColliderMeshData }
  | { status: 'error' };
const meshCache = new Map<string, CacheEntry>();

export function setColliderMeshUrl(
  state: State,
  entity: number,
  url: string
): void {
  let m = urlByState.get(state);
  if (!m) {
    m = new Map();
    urlByState.set(state, m);
  }
  m.set(entity, url.trim());
}

export function getColliderMeshUrl(
  state: State,
  entity: number
): string | undefined {
  return urlByState.get(state)?.get(entity);
}

/**
 * Returns the parsed collision mesh for `url`, kicking off the fetch on first
 * call. `null` while loading; `'error'` status is sticky (warned once).
 */
export function requestColliderMesh(url: string): ColliderMeshData | null {
  const entry = meshCache.get(url);
  if (entry) {
    return entry.status === 'ready' ? entry.data : null;
  }

  meshCache.set(url, { status: 'loading' });
  void fetch(url)
    .then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = parseGlbCollisionMesh(await res.arrayBuffer());
      meshCache.set(url, { status: 'ready', data });
    })
    .catch((err: unknown) => {
      meshCache.set(url, { status: 'error' });
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`[mesh-collider] failed to load "${url}": ${msg}`);
    });
  return null;
}

/** Whether the URL failed to load (collider creation should give up). */
export function colliderMeshFailed(url: string): boolean {
  return meshCache.get(url)?.status === 'error';
}

const _aabb = new THREE.Box3();
const _v = new THREE.Vector3();

/**
 * Apply uniform scale + anchor to the cached mesh, producing the geometry
 * Rapier consumes. Returns fresh arrays — Rapier keeps a reference.
 */
export function buildMeshColliderGeometry(
  data: ColliderMeshData,
  scale: number,
  anchor: number
): ColliderMeshData {
  const s = scale > 0 ? scale : 1;
  const vertices = new Float32Array(data.vertices.length);
  for (let i = 0; i < data.vertices.length; i++) {
    vertices[i] = data.vertices[i] * s;
  }

  if (anchor === MeshAnchor.Base) {
    _aabb.makeEmpty();
    for (let i = 0; i < vertices.length; i += 3) {
      _v.set(vertices[i], vertices[i + 1], vertices[i + 2]);
      _aabb.expandByPoint(_v);
    }
    const cx = (_aabb.min.x + _aabb.max.x) / 2;
    const cz = (_aabb.min.z + _aabb.max.z) / 2;
    const minY = _aabb.min.y;
    for (let i = 0; i < vertices.length; i += 3) {
      vertices[i] -= cx;
      vertices[i + 1] -= minY;
      vertices[i + 2] -= cz;
    }
  }

  return { vertices, indices: data.indices.slice() };
}

// --- minimal GLB parsing ---------------------------------------------------

interface GltfJson {
  scenes?: Array<{ nodes?: number[] }>;
  scene?: number;
  nodes?: Array<{
    children?: number[];
    mesh?: number;
    matrix?: number[];
    translation?: number[];
    rotation?: number[];
    scale?: number[];
  }>;
  meshes?: Array<{
    primitives: Array<{
      attributes: Record<string, number>;
      indices?: number;
      mode?: number;
    }>;
  }>;
  accessors?: Array<{
    bufferView?: number;
    byteOffset?: number;
    componentType: number;
    count: number;
    type: string;
    sparse?: unknown;
  }>;
  bufferViews?: Array<{
    buffer: number;
    byteOffset?: number;
    byteLength: number;
    byteStride?: number;
  }>;
  buffers?: Array<{ uri?: string; byteLength: number }>;
}

export function parseGlbCollisionMesh(buffer: ArrayBuffer): ColliderMeshData {
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546c67) {
    throw new Error('not a GLB (bad magic)');
  }

  let json: GltfJson | null = null;
  let bin: ArrayBuffer | null = null;
  let offset = 12;
  while (offset < buffer.byteLength) {
    const chunkLength = view.getUint32(offset, true);
    const chunkType = view.getUint32(offset + 4, true);
    const chunkStart = offset + 8;
    if (chunkType === 0x4e4f534a) {
      const text = new TextDecoder().decode(
        new Uint8Array(buffer, chunkStart, chunkLength)
      );
      json = JSON.parse(text) as GltfJson;
    } else if (chunkType === 0x004e4942) {
      bin = buffer.slice(chunkStart, chunkStart + chunkLength);
    }
    offset = chunkStart + chunkLength;
  }
  if (!json) throw new Error('GLB has no JSON chunk');

  const positions: number[] = [];
  const indices: number[] = [];
  const matrix = new THREE.Matrix4();
  const vert = new THREE.Vector3();

  const visitNode = (nodeIndex: number, parent: THREE.Matrix4): void => {
    const node = json.nodes?.[nodeIndex];
    if (!node) return;

    const local = new THREE.Matrix4();
    if (node.matrix) {
      local.fromArray(node.matrix);
    } else {
      const t = node.translation ?? [0, 0, 0];
      const r = node.rotation ?? [0, 0, 0, 1];
      const s = node.scale ?? [1, 1, 1];
      local.compose(
        new THREE.Vector3(t[0], t[1], t[2]),
        new THREE.Quaternion(r[0], r[1], r[2], r[3]),
        new THREE.Vector3(s[0], s[1], s[2])
      );
    }
    const world = new THREE.Matrix4().multiplyMatrices(parent, local);

    if (node.mesh !== undefined) {
      const mesh = json.meshes?.[node.mesh];
      for (const prim of mesh?.primitives ?? []) {
        if (prim.mode !== undefined && prim.mode !== 4) continue; // triangles only
        const base = positions.length / 3;
        appendPositions(json, bin, prim.attributes.POSITION, world, positions);
        appendIndices(json, bin, prim.indices, base, positions, indices);
      }
    }

    for (const child of node.children ?? []) visitNode(child, world);
  };

  matrix.identity();
  const sceneNodes =
    json.scenes?.[json.scene ?? 0]?.nodes ??
    (json.nodes ? json.nodes.map((_, i) => i) : []);
  for (const n of sceneNodes) visitNode(n, matrix);

  if (positions.length === 0) {
    throw new Error('GLB contains no triangle geometry');
  }
  return {
    vertices: new Float32Array(positions),
    indices: new Uint32Array(indices),
  };

  function appendPositions(
    gltf: GltfJson,
    binChunk: ArrayBuffer | null,
    accessorIndex: number | undefined,
    world: THREE.Matrix4,
    out: number[]
  ): void {
    if (accessorIndex === undefined) return;
    const acc = gltf.accessors?.[accessorIndex];
    if (!acc || acc.type !== 'VEC3' || acc.componentType !== 5126) {
      throw new Error('POSITION accessor must be float32 VEC3');
    }
    if (acc.sparse) throw new Error('sparse accessors not supported');
    const bv = gltf.bufferViews?.[acc.bufferView ?? -1];
    if (!bv || !binChunk) throw new Error('POSITION data missing BIN chunk');
    if (gltf.buffers?.[bv.buffer]?.uri) {
      throw new Error('external buffers not supported');
    }
    const stride = bv.byteStride ?? 12;
    const start = (bv.byteOffset ?? 0) + (acc.byteOffset ?? 0);
    const dv = new DataView(binChunk);
    for (let i = 0; i < acc.count; i++) {
      const o = start + i * stride;
      vert
        .set(
          dv.getFloat32(o, true),
          dv.getFloat32(o + 4, true),
          dv.getFloat32(o + 8, true)
        )
        .applyMatrix4(world);
      out.push(vert.x, vert.y, vert.z);
    }
  }

  function appendIndices(
    gltf: GltfJson,
    binChunk: ArrayBuffer | null,
    accessorIndex: number | undefined,
    baseVertex: number,
    allPositions: number[],
    out: number[]
  ): void {
    if (accessorIndex === undefined) {
      // non-indexed triangles: index the freshly appended vertices in order
      const newVerts = allPositions.length / 3 - baseVertex;
      for (let i = 0; i < newVerts; i++) out.push(baseVertex + i);
      return;
    }
    const acc = gltf.accessors?.[accessorIndex];
    if (!acc || acc.type !== 'SCALAR') {
      throw new Error('index accessor must be SCALAR');
    }
    if (acc.sparse) throw new Error('sparse accessors not supported');
    const bv = gltf.bufferViews?.[acc.bufferView ?? -1];
    if (!bv || !binChunk) throw new Error('index data missing BIN chunk');
    const start = (bv.byteOffset ?? 0) + (acc.byteOffset ?? 0);
    const dv = new DataView(binChunk);
    for (let i = 0; i < acc.count; i++) {
      let idx: number;
      if (acc.componentType === 5125) idx = dv.getUint32(start + i * 4, true);
      else if (acc.componentType === 5123)
        idx = dv.getUint16(start + i * 2, true);
      else if (acc.componentType === 5121) idx = dv.getUint8(start + i);
      else
        throw new Error(`unsupported index componentType ${acc.componentType}`);
      out.push(baseVertex + idx);
    }
  }
}
