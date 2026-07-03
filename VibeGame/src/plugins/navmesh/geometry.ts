import * as THREE from 'three';
import { defineQuery } from '../../core';
import type { State } from '../../core';
import {
  CompositionPending,
  getCompositionData,
  type PrimitiveSpec,
} from '../composition';
import {
  buildMeshColliderGeometry,
  getColliderMeshUrl,
  requestColliderMesh,
} from '../physics/mesh-collider';
import {
  BodyType,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../physics/components';
import { Transform } from '../transforms/components';
import { sampleHeightAt } from '../terrain/height-sampler';
import type { HeightSampler } from '../terrain/height-sampler';
import { getTerrainContext } from '../terrain/utils';
import { getWaterBodies } from '../water/registry';

export interface NavMeshGeometry {
  positions: Float32Array;
  indices: Uint32Array;
}

function buildTerrainGeometry(
  state: State,
  divisions: number,
  bounds: number
): NavMeshGeometry | null {
  const ctx = getTerrainContext(state);
  let terrainSampler: HeightSampler | null = null;
  let worldOffset = { x: 0, y: 0, z: 0 };

  for (const [, data] of ctx) {
    if (!data.initialized) continue;
    terrainSampler = data.sampler;
    worldOffset = data.worldOffset;
    break;
  }

  if (!terrainSampler) return null;

  const half = bounds;
  const step = (bounds * 2) / divisions;
  const verts = divisions + 1;
  const positions = new Float32Array(verts * verts * 3);
  const indices = new Uint32Array(divisions * divisions * 6);

  let pi = 0;
  for (let z = 0; z < verts; z++) {
    for (let x = 0; x < verts; x++) {
      const wx = -half + x * step;
      const wz = -half + z * step;
      const wy =
        sampleHeightAt(terrainSampler, wx - worldOffset.x, wz - worldOffset.z) +
        worldOffset.y;
      positions[pi++] = wx;
      positions[pi++] = wy;
      positions[pi++] = wz;
    }
  }

  let ti = 0;
  for (let z = 0; z < divisions; z++) {
    for (let x = 0; x < divisions; x++) {
      const a = z * verts + x;
      const b = a + 1;
      const c = a + verts;
      const d = c + 1;
      indices[ti++] = a;
      indices[ti++] = c;
      indices[ti++] = b;
      indices[ti++] = b;
      indices[ti++] = c;
      indices[ti++] = d;
    }
  }

  return { positions, indices };
}

interface MeshSoup {
  positions: number[];
  indices: number[];
}

/**
 * Only collider geometry within this height of an obstacle's base carves the
 * navmesh. Obstacle colliders are convex hulls (tree = trunk-to-canopy blob);
 * baking the whole hull made the flared upper part a walkable ramp that agents
 * climbed at the corners. Capping to roughly the agent height keeps the lower
 * trunk footprint (a clean hole to walk around) and drops the canopy, so agents
 * path UNDER the leaves instead of over them. Physics still uses the full hull.
 */
const OBSTACLE_NAV_HEIGHT = 2.5;

const _bodyMat = new THREE.Matrix4();
const _offsetMat = new THREE.Matrix4();
const _worldMat = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3(1, 1, 1);
const _v = new THREE.Vector3();

/** Compose an obstacle's collider→world matrix into `_worldMat`. The collider
 * geometry is authored in body-local space, offset by the collider's local
 * pos/rot, then placed by the rigidbody's world pose — exactly how the physics
 * step positions it. */
function obstacleWorldMatrix(state: State, eid: number): THREE.Matrix4 {
  if (state.hasComponent(eid, Rigidbody)) {
    _pos.set(Rigidbody.posX[eid], Rigidbody.posY[eid], Rigidbody.posZ[eid]);
    const rw = Rigidbody.rotW[eid];
    if (
      rw === 0 &&
      Rigidbody.rotX[eid] === 0 &&
      Rigidbody.rotY[eid] === 0 &&
      Rigidbody.rotZ[eid] === 0
    ) {
      _quat.identity();
    } else {
      _quat.set(
        Rigidbody.rotX[eid],
        Rigidbody.rotY[eid],
        Rigidbody.rotZ[eid],
        rw
      );
    }
  } else {
    _pos.set(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
    _quat.set(
      Transform.rotX[eid],
      Transform.rotY[eid],
      Transform.rotZ[eid],
      Transform.rotW[eid] || 1
    );
  }
  _bodyMat.compose(_pos, _quat, _scl);

  // Collider local offset (translation + rotation).
  _pos.set(
    Collider.posOffsetX[eid],
    Collider.posOffsetY[eid],
    Collider.posOffsetZ[eid]
  );
  const orw = Collider.rotOffsetW[eid];
  if (
    orw === 0 &&
    Collider.rotOffsetX[eid] === 0 &&
    Collider.rotOffsetY[eid] === 0 &&
    Collider.rotOffsetZ[eid] === 0
  ) {
    _quat.identity();
  } else {
    _quat.set(
      Collider.rotOffsetX[eid],
      Collider.rotOffsetY[eid],
      Collider.rotOffsetZ[eid],
      orw
    );
  }
  _offsetMat.compose(_pos, _quat, _scl);

  return _worldMat.multiplyMatrices(_bodyMat, _offsetMat);
}

function appendMesh(
  vertices: ArrayLike<number>,
  indices: ArrayLike<number>,
  matrix: THREE.Matrix4,
  out: MeshSoup,
  fullCarve = false
): void {
  const base = out.positions.length / 3;
  let minY = Infinity;
  for (let i = 0; i < vertices.length; i += 3) {
    _v.set(vertices[i], vertices[i + 1], vertices[i + 2]).applyMatrix4(matrix);
    out.positions.push(_v.x, _v.y, _v.z);
    if (_v.y < minY) minY = _v.y;
  }
  // fullCarve skips the per-mesh height cap: composition primitives are
  // standalone obstacles (walls/roof), so the entire mesh must carve the
  // navmesh or agents end up walking on top of the structure.
  const maxY = fullCarve ? Infinity : minY + OBSTACLE_NAV_HEIGHT;
  for (let t = 0; t < indices.length; t += 3) {
    const a = indices[t];
    const b = indices[t + 1];
    const c = indices[t + 2];
    const ay = out.positions[(base + a) * 3 + 1];
    const by = out.positions[(base + b) * 3 + 1];
    const cy = out.positions[(base + c) * 3 + 1];
    if (ay > maxY && by > maxY && cy > maxY) continue;
    out.indices.push(base + a, base + b, base + c);
  }
}

// 8 corners + 12 triangles of a unit box centered at origin (half extent 0.5).
const BOX_CORNERS = [
  -0.5, -0.5, -0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, -0.5, -0.5,
  -0.5, 0.5, 0.5, -0.5, 0.5, 0.5, 0.5, 0.5, -0.5, 0.5, 0.5,
];
const BOX_INDICES = [
  0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 1, 2,
  6, 1, 6, 5, 0, 4, 7, 0, 7, 3,
];
const _boxVerts = new Float32Array(24);

function appendBox(eid: number, matrix: THREE.Matrix4, out: MeshSoup): void {
  const sx = Collider.sizeX[eid] * (Transform.scaleX[eid] || 1);
  const sy = Collider.sizeY[eid] * (Transform.scaleY[eid] || 1);
  const sz = Collider.sizeZ[eid] * (Transform.scaleZ[eid] || 1);
  for (let i = 0; i < 8; i++) {
    _boxVerts[i * 3] = BOX_CORNERS[i * 3] * sx;
    _boxVerts[i * 3 + 1] = BOX_CORNERS[i * 3 + 1] * sy;
    _boxVerts[i * 3 + 2] = BOX_CORNERS[i * 3 + 2] * sz;
  }
  appendMesh(_boxVerts, BOX_INDICES, matrix, out);
}

const obstacleQuery = defineQuery([Collider]);
const compositionObstacleQuery = defineQuery([CompositionPending]);

const _compBodyMat = new THREE.Matrix4();
const _compLocalMat = new THREE.Matrix4();
const _compWorldMat = new THREE.Matrix4();
const _compPos = new THREE.Vector3();
const _compQuat = new THREE.Quaternion();
const _compEuler = new THREE.Euler();
const _compScl = new THREE.Vector3(1, 1, 1);

// Composition entities own their collider via direct Rapier calls (no SOA
// `Collider` component), so the navmesh has to read primitive specs from the
// composition sidecar and bake them as box envelopes here.
function compositionEntityWorldMatrix(
  state: State,
  eid: number
): THREE.Matrix4 {
  if (state.hasComponent(eid, Rigidbody)) {
    _compPos.set(Rigidbody.posX[eid], Rigidbody.posY[eid], Rigidbody.posZ[eid]);
    const rw = Rigidbody.rotW[eid];
    if (
      rw === 0 &&
      Rigidbody.rotX[eid] === 0 &&
      Rigidbody.rotY[eid] === 0 &&
      Rigidbody.rotZ[eid] === 0
    ) {
      _compQuat.identity();
    } else {
      _compQuat.set(
        Rigidbody.rotX[eid],
        Rigidbody.rotY[eid],
        Rigidbody.rotZ[eid],
        rw
      );
    }
  } else {
    _compPos.set(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]);
    const rw = Transform.rotW[eid] || 1;
    _compQuat.set(
      Transform.rotX[eid],
      Transform.rotY[eid],
      Transform.rotZ[eid],
      rw
    );
  }
  _compBodyMat.compose(_compPos, _compQuat, _compScl);
  return _compBodyMat;
}

function appendCompositionSpec(
  spec: PrimitiveSpec,
  parentMatrix: THREE.Matrix4,
  out: MeshSoup
): void {
  const sx = Math.max(spec.sizeX, 1e-4);
  const sy = Math.max(spec.sizeY, 1e-4);
  const sz = Math.max(spec.sizeZ, 1e-4);
  for (let i = 0; i < 8; i++) {
    _boxVerts[i * 3] = BOX_CORNERS[i * 3] * sx;
    _boxVerts[i * 3 + 1] = BOX_CORNERS[i * 3 + 1] * sy;
    _boxVerts[i * 3 + 2] = BOX_CORNERS[i * 3 + 2] * sz;
  }
  _compPos.set(spec.posX, spec.posY, spec.posZ);
  _compEuler.set(spec.rotX, spec.rotY, spec.rotZ);
  _compQuat.setFromEuler(_compEuler);
  _compLocalMat.compose(_compPos, _compQuat, _compScl);
  _compWorldMat.multiplyMatrices(parentMatrix, _compLocalMat);
  appendMesh(_boxVerts, BOX_INDICES, _compWorldMat, out, true);
}

/** True if every fixed trimesh/convex obstacle's collision GLB has finished
 * loading — the navmesh defers baking until then so holes aren't missed. */
export function navmeshObstaclesLoaded(state: State, bounds: number): boolean {
  for (const eid of obstacleQuery(state.world)) {
    if (!isFixedObstacle(state, eid)) continue;
    if (!withinBounds(state, eid, bounds)) continue;
    const shape = Collider.shape[eid];
    if (shape !== ColliderShape.TriMesh && shape !== ColliderShape.ConvexHull) {
      continue;
    }
    const url = getColliderMeshUrl(state, eid);
    if (!url) continue;
    if (!requestColliderMesh(url)) return false;
  }
  for (const eid of compositionObstacleQuery(state.world)) {
    if (!withinBounds(state, eid, bounds)) continue;
    if (CompositionPending.colliderBuilt[eid] !== 1) return false;
  }
  return true;
}

function isFixedObstacle(state: State, eid: number): boolean {
  if (Collider.isSensor[eid] === 1) return false;
  if (
    state.hasComponent(eid, Rigidbody) &&
    Rigidbody.type[eid] !== BodyType.Fixed
  ) {
    return false;
  }
  const shape = Collider.shape[eid];
  return (
    shape === ColliderShape.Box ||
    shape === ColliderShape.TriMesh ||
    shape === ColliderShape.ConvexHull
  );
}

function withinBounds(state: State, eid: number, bounds: number): boolean {
  const x = state.hasComponent(eid, Rigidbody)
    ? Rigidbody.posX[eid]
    : Transform.posX[eid];
  const z = state.hasComponent(eid, Rigidbody)
    ? Rigidbody.posZ[eid]
    : Transform.posZ[eid];
  return Math.abs(x) <= bounds + 10 && Math.abs(z) <= bounds + 10;
}

/** Bake every fixed Box/TriMesh/ConvexHull collider within `bounds` into one
 * mesh. Colliders are the single source of truth for what blocks movement, so
 * the navmesh carves holes exactly where physics blocks the player. */
function collectColliderObstacles(
  state: State,
  bounds: number
): NavMeshGeometry | null {
  const soup: MeshSoup = { positions: [], indices: [] };

  for (const eid of obstacleQuery(state.world)) {
    if (!isFixedObstacle(state, eid)) continue;
    if (!withinBounds(state, eid, bounds)) continue;

    const matrix = obstacleWorldMatrix(state, eid);
    const shape = Collider.shape[eid];

    if (shape === ColliderShape.Box) {
      appendBox(eid, matrix, soup);
      continue;
    }

    const url = getColliderMeshUrl(state, eid);
    if (!url) continue;
    const data = requestColliderMesh(url);
    if (!data) continue;
    const scale = (Collider.meshScale[eid] || 1) * (Transform.scaleX[eid] || 1);
    const baked = buildMeshColliderGeometry(
      data,
      scale,
      Collider.meshAnchor[eid]
    );
    appendMesh(baked.vertices, baked.indices, matrix, soup);
  }

  for (const eid of compositionObstacleQuery(state.world)) {
    if (CompositionPending.colliderBuilt[eid] !== 1) continue;
    if (!withinBounds(state, eid, bounds)) continue;
    if (
      state.hasComponent(eid, Rigidbody) &&
      Rigidbody.type[eid] !== BodyType.Fixed
    ) {
      continue;
    }
    const data = getCompositionData(state, eid);
    if (!data || data.colliderMode === 'none') continue;
    const matrix = compositionEntityWorldMatrix(state, eid);
    for (const spec of data.specs) {
      appendCompositionSpec(spec, matrix, soup);
    }
  }

  if (soup.indices.length === 0) return null;
  return {
    positions: new Float32Array(soup.positions),
    indices: new Uint32Array(soup.indices),
  };
}

// Radial resolution of the lake-exclusion cylinder. 16 segments is a smooth
// circle at the typical lake radii (<= ~10 m) and stays cheap: few lakes exist.
const WATER_CYLINDER_SEGMENTS = 16;

// Lake wall band: straddles the water surface so the vertical face reliably
// intersects the walkable terrain skin. Below the surface reaches under the
// carved basin floor; above clears the agent height (~1.0 m).
const WATER_WALL_DROP = 1.0;
const WATER_WALL_RISE = 1.5;

const _waterMat = new THREE.Matrix4();
const _waterPos = new THREE.Vector3();
const _waterQuat = new THREE.Quaternion();
const _waterScl = new THREE.Vector3(1, 1, 1);

/** Bake a short vertical cylinder per registered lake so the recast voxelizer
 * carves a hole through the water footprint. The Water plugin already lowers
 * the terrain into a bowl, but a default lake (depth 1.5, radius 6) has a rim
 * gradient of only ~26.6° — well under the 45° `walkableSlopeAngle` — so the
 * basin stays WALKABLE and NPCs path straight through the water. recast only
 * carves non-walkable cells from input geometry steeper than the walkable
 * slope, so we feed it a 90° wall (an open cylinder tube) at each lake centre.
 * `fullCarve` is set so the per-mesh height cap cannot clip the wall away. */
export function collectWaterObstacles(
  state: State,
  bounds: number
): NavMeshGeometry | null {
  const bodies = getWaterBodies(state);
  if (bodies.length === 0) return null;

  const soup: MeshSoup = { positions: [], indices: [] };
  const seg = WATER_CYLINDER_SEGMENTS;
  const verts = new Float32Array(seg * 2 * 3);
  const wallIndices: number[] = [];
  for (let i = 0; i < seg; i++) {
    const ni = (i + 1) % seg;
    const b0 = i;
    const b1 = ni;
    const t0 = seg + i;
    const t1 = seg + ni;
    wallIndices.push(b0, b1, t1, b0, t1, t0);
  }

  for (const body of bodies) {
    // Keep a lake only when its disc intersects the square bake area.
    if (Math.abs(body.x) - body.radius > bounds) continue;
    if (Math.abs(body.z) - body.radius > bounds) continue;

    const yBottom = body.waterY - WATER_WALL_DROP;
    const yTop = body.waterY + WATER_WALL_RISE;

    for (let i = 0; i < seg; i++) {
      const a = (i / seg) * Math.PI * 2;
      const cx = Math.cos(a) * body.radius;
      const cz = Math.sin(a) * body.radius;
      verts[i * 3] = cx;
      verts[i * 3 + 1] = yBottom;
      verts[i * 3 + 2] = cz;
      verts[(seg + i) * 3] = cx;
      verts[(seg + i) * 3 + 1] = yTop;
      verts[(seg + i) * 3 + 2] = cz;
    }

    _waterPos.set(body.x, 0, body.z);
    _waterQuat.identity();
    _waterScl.set(1, 1, 1);
    _waterMat.compose(_waterPos, _waterQuat, _waterScl);
    appendMesh(verts, wallIndices, _waterMat, soup, true);
  }

  if (soup.indices.length === 0) return null;
  return {
    positions: new Float32Array(soup.positions),
    indices: new Uint32Array(soup.indices),
  };
}

export function collectNavmeshGeometry(
  state: State,
  terrainDivisions = 128,
  bounds = 120
): NavMeshGeometry {
  const parts: NavMeshGeometry[] = [];

  const terrain = buildTerrainGeometry(state, terrainDivisions, bounds);
  if (terrain) parts.push(terrain);

  const obstacles = collectColliderObstacles(state, bounds);
  if (obstacles) parts.push(obstacles);

  const water = collectWaterObstacles(state, bounds);
  if (water) parts.push(water);

  if (parts.length === 0) {
    return { positions: new Float32Array(0), indices: new Uint32Array(0) };
  }

  let totalVerts = 0;
  let totalIndices = 0;
  for (const p of parts) {
    totalVerts += p.positions.length / 3;
    totalIndices += p.indices.length;
  }

  const positions = new Float32Array(totalVerts * 3);
  const indices = new Uint32Array(totalIndices);
  let vBase = 0;
  let iBase = 0;

  for (const p of parts) {
    positions.set(p.positions, vBase * 3);
    for (let i = 0; i < p.indices.length; i++) {
      indices[iBase++] = p.indices[i] + vBase;
    }
    vBase += p.positions.length / 3;
  }

  return { positions, indices };
}
