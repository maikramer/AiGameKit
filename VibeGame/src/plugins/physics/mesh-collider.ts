import { logger } from '../../core/utils/logger';
import {
  fetchBytesResilient,
  isPermanentFetchError,
} from '../../core/utils/resilient-net';
import { createGeometryGLTFLoader } from '../../extras/gltf-bridge';
import * as THREE from 'three';
import type { State } from '../../core';

/**
 * Collision-mesh colliders (`collider="shape: trimesh; mesh-url: …"`).
 *
 * Loads a GLB and extracts node-transformed POSITION + index data for
 * Rapier's trimesh/convexHull descriptors.
 *
 * GLBs with `EXT_meshopt_compression` (the default for LODs from `text3d lod
 * --meshopt`) are decoded via THREE.GLTFLoader + MeshoptDecoder. Plain GLBs
 * (including the tiny dedicated `*_collision.glb` hulls) take a fast manual
 * path that avoids constructing a scene graph.
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
  | {
      status: 'error';
      permanent: boolean;
      attempts: number;
      retryAtMs: number;
    };
const meshCache = new Map<string, CacheEntry>();

/** Delay determinístico entre ciclos de re-tentativa de um GLB transitório. */
export const MESH_RETRY_BASE_MS = 2_000;
export const MESH_RETRY_MAX_MS = 30_000;
export const MAX_MESH_RETRY_CYCLES = 5;

export function meshRetryDelayMs(attempts: number): number {
  return Math.min(MESH_RETRY_MAX_MS, MESH_RETRY_BASE_MS * 2 ** attempts);
}

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
 * call. `null` while loading or waiting out a transient-failure backoff;
 * permanent failures (404, undecodable GLB, exhausted retries) are sticky.
 */
export function requestColliderMesh(url: string): ColliderMeshData | null {
  const entry = meshCache.get(url);
  if (entry) {
    if (entry.status === 'ready') return entry.data;
    if (entry.status === 'loading') return null;
    if (entry.permanent || Date.now() < entry.retryAtMs) return null;
    if (entry.attempts >= MAX_MESH_RETRY_CYCLES) {
      entry.permanent = true;
      return null;
    }
    entry.attempts += 1;
    entry.retryAtMs = Date.now() + meshRetryDelayMs(entry.attempts);
  }
  void loadColliderMesh(
    url,
    entry && entry.status === 'error' ? entry.attempts : 0
  );
  return null;
}

/** Whether the URL is permanently unloadable (collider creation should give up). */
export function colliderMeshFailed(url: string): boolean {
  const entry = meshCache.get(url);
  return entry !== undefined && entry.status === 'error' && entry.permanent;
}

async function loadColliderMesh(url: string, attempts: number): Promise<void> {
  meshCache.set(url, { status: 'loading' });
  try {
    const buf = await fetchBytesResilient(url);
    const data = await loadGlbCollisionMesh(buf.buffer as ArrayBuffer, url);
    meshCache.set(url, { status: 'ready', data });
  } catch (err) {
    // A fetched GLB that parses but has no geometry is a broken asset, not a
    // flaky network — retrying will not fix it.
    const brokenAsset =
      err instanceof Error && /no triangle geometry/.test(err.message);
    const permanent = brokenAsset || isPermanentFetchError(err);
    const nextAttempts = attempts + 1;
    const giveUp = permanent || nextAttempts >= MAX_MESH_RETRY_CYCLES;
    meshCache.set(url, {
      status: 'error',
      permanent: giveUp,
      attempts: nextAttempts,
      retryAtMs: Date.now() + meshRetryDelayMs(nextAttempts),
    });
    const msg = err instanceof Error ? err.message : String(err);
    if (giveUp) {
      logger.error(
        `[mesh-collider] failed to load "${url}" permanently: ${msg}`
      );
    } else {
      logger.warn(
        `[mesh-collider] transient failure for "${url}" (${msg}) — retry ${nextAttempts}/${MAX_MESH_RETRY_CYCLES}`
      );
    }
  }
}

/** Util para testes: limpa o cache global de meshes de colisão. */
export function resetColliderMeshCacheForTests(): void {
  meshCache.clear();
}

const _aabb = new THREE.Box3();
const _v = new THREE.Vector3();

/**
 * Apply uniform scale + anchor to the cached mesh, producing the geometry
 * Rapier consumes. Returns fresh arrays — Rapier keeps a reference.
 */
/** Per-axis scale for trimesh verts (matches Transform.scaleX/Y/Z). */
export type MeshColliderScale = number | { x: number; y: number; z: number };

function resolveMeshScale(scale: MeshColliderScale): {
  x: number;
  y: number;
  z: number;
} {
  if (typeof scale === 'number') {
    const s = scale > 0 ? scale : 1;
    return { x: s, y: s, z: s };
  }
  return {
    x: scale.x > 0 ? scale.x : 1,
    y: scale.y > 0 ? scale.y : 1,
    z: scale.z > 0 ? scale.z : 1,
  };
}

/**
 * Builds Rapier-ready verts from a loaded collision mesh.
 * Accepts uniform number or `{x,y,z}` — bridges stretch only in X; applying
 * `scaleX` to Y/Z inflated the hull into a ghost walk surface above the deck.
 */
export function buildMeshColliderGeometry(
  data: ColliderMeshData,
  scale: MeshColliderScale,
  anchor: number
): ColliderMeshData {
  const { x: sx, y: sy, z: sz } = resolveMeshScale(scale);
  const vertices = new Float32Array(data.vertices.length);
  for (let i = 0; i < vertices.length; i += 3) {
    vertices[i] = data.vertices[i]! * sx;
    vertices[i + 1] = data.vertices[i + 1]! * sy;
    vertices[i + 2] = data.vertices[i + 2]! * sz;
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

// --- meshopt detection + GLTFLoader path ----------------------------------

/**
 * Peek at the GLB JSON chunk to check whether the manual float32 POSITION
 * parser can read it. Meshopt / mesh_quantization need THREE.GLTFLoader.
 */
export function glbNeedsGeometryLoader(buffer: ArrayBuffer): boolean {
  try {
    const view = new DataView(buffer);
    if (view.getUint32(0, true) !== 0x46546c67) return false;
    let offset = 12;
    while (offset < buffer.byteLength) {
      const chunkLength = view.getUint32(offset, true);
      const chunkType = view.getUint32(offset + 4, true);
      if (chunkType === 0x4e4f534a) {
        const text = new TextDecoder().decode(
          new Uint8Array(buffer, offset + 8, chunkLength)
        );
        const json = JSON.parse(text) as {
          extensionsUsed?: string[];
          meshes?: Array<{
            primitives?: Array<{ attributes?: Record<string, number> }>;
          }>;
          accessors?: Array<{
            componentType?: number;
            type?: string;
          }>;
        };
        const used = json.extensionsUsed ?? [];
        if (
          used.includes('EXT_meshopt_compression') ||
          used.includes('KHR_mesh_quantization')
        ) {
          return true;
        }
        for (const mesh of json.meshes ?? []) {
          for (const prim of mesh.primitives ?? []) {
            const posIdx = prim.attributes?.POSITION;
            if (posIdx === undefined) continue;
            const acc = json.accessors?.[posIdx];
            if (!acc) continue;
            if (acc.type !== 'VEC3' || acc.componentType !== 5126) return true;
          }
        }
        return false;
      }
      offset = offset + 8 + chunkLength;
    }
  } catch {
    // malformed — let the parser emit the real error
  }
  return false;
}

/**
 * Decode a meshopt/quantized GLB via THREE.GLTFLoader (MeshoptDecoder), then
 * extract world-space POSITION + index data. Textures are never fetched.
 */
async function parseGlbCollisionMeshViaLoader(
  buffer: ArrayBuffer,
  url: string
): Promise<ColliderMeshData> {
  const loader = createGeometryGLTFLoader();
  const gltf = await loader.parseAsync(buffer, url);

  const positions: number[] = [];
  const indices: number[] = [];
  const vert = new THREE.Vector3();

  gltf.scene.updateMatrixWorld(true);
  gltf.scene.traverse((obj) => {
    if ((obj as THREE.Mesh).isMesh !== true) return;
    const mesh = obj as THREE.Mesh;
    mesh.updateMatrixWorld(true);
    const geom = mesh.geometry;
    const posAttr = geom.getAttribute('position');
    if (!posAttr) return;
    const base = positions.length / 3;
    for (let i = 0; i < posAttr.count; i++) {
      vert.fromBufferAttribute(posAttr, i).applyMatrix4(mesh.matrixWorld);
      positions.push(vert.x, vert.y, vert.z);
    }
    const idxAttr = geom.index;
    if (idxAttr) {
      for (let i = 0; i < idxAttr.count; i++)
        indices.push(base + idxAttr.getX(i));
    } else {
      for (let i = 0; i < posAttr.count; i++) indices.push(base + i);
    }
  });

  // Free GPU/CPU resources held by the transient loader result.
  gltf.scene.traverse((obj) => {
    const mesh = obj as THREE.Mesh;
    if (mesh.isMesh) mesh.geometry?.dispose();
  });

  if (positions.length === 0) {
    throw new Error('GLB contains no triangle geometry');
  }
  return {
    vertices: new Float32Array(positions),
    indices: new Uint32Array(indices),
  };
}

/**
 * Load collision geometry from a GLB buffer, using the fast manual parser for
 * plain float32 meshes and THREE.GLTFLoader + MeshoptDecoder for meshopt /
 * quantized deliverables (LOD0 from `text3d lod --meshopt`).
 */
export async function loadGlbCollisionMesh(
  buffer: ArrayBuffer,
  url = 'collision.glb'
): Promise<ColliderMeshData> {
  if (glbNeedsGeometryLoader(buffer)) {
    return parseGlbCollisionMeshViaLoader(buffer, url);
  }
  try {
    return parseGlbCollisionMesh(buffer);
  } catch (err) {
    // Quantized / unexpected accessors that the peek missed — fall back.
    try {
      return await parseGlbCollisionMeshViaLoader(buffer, url);
    } catch {
      throw err;
    }
  }
}

// --- minimal GLB parsing (fast path for uncompressed collision hulls) ------

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
