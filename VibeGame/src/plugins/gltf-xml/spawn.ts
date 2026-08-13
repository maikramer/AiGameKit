import type { State } from '../../core';
import { DistanceCull } from '../rendering/components';
import { Transform } from '../transforms/components';
import { setGltfUrl } from './context';
import { GltfPending } from './components';
import {
  markGltfInstanced,
  setInstancedLodThreshold,
  setInstancedLodUrl,
} from './auto-instance';

/**
 * Place one instance of a GLB from code, through the shared instancing pool.
 *
 * `<GLTFLoader instanced="true">` is the declarative form; this is the same
 * thing for props whose positions are computed rather than authored — track
 * dressing walked along a spline, debris scattered by a script, a wall built
 * from a generated layout.
 *
 * The alternative games reach for is cloning the loaded scene per prop, and it
 * is a trap at scale: 1500 barriers along a 5 km circuit become 1500 draw calls
 * and 1500 objects for three.js to cull and sort every frame. Routed through
 * the pool they cost one draw call per GLB primitive, and `InstancedMesh2`
 * frustum-culls and LOD-switches each instance for free.
 */
export interface InstancedGltfSpawn {
  /** LOD0 GLB url. */
  url: string;
  /** Optional LOD1 / LOD2 urls — chained onto the same pool. */
  lod1Url?: string;
  lod2Url?: string;
  /** World position. */
  x: number;
  y: number;
  z: number;
  /** Yaw in radians (props only ever need the Y axis). */
  yaw?: number;
  /**
   * Full orientation as `[x, y, z, w]`, for props that also need pitch/roll —
   * a model that had to be stood up by `fitModel`, debris thrown by a script.
   * Wins over {@link yaw} when both are given.
   */
  quaternion?: readonly [number, number, number, number];
  /** Uniform scale; use the GLB's own size when omitted. */
  scale?: number;
  /** Distance (m) at which LOD1 / LOD2 take over for this pool. */
  lodNear?: number;
  lodMid?: number;
  /** Hide the instance past this distance (m). 0 / omitted = never. */
  cullDistance?: number;
  /** Optional entity name (debug bridge, lookups). */
  name?: string;
}

/**
 * Create the entity and return its id. The GLB is fetched once per url by the
 * pool, so calling this a thousand times costs one load.
 *
 * The instance appears on the frame after the loader system runs — like every
 * other GLTF entity, nothing renders synchronously here.
 */
export function spawnInstancedGltf(
  state: State,
  spawn: InstancedGltfSpawn
): number {
  const eid = state.createEntity();
  state.addComponent(eid, Transform);
  Transform.posX[eid] = spawn.x;
  Transform.posY[eid] = spawn.y;
  Transform.posZ[eid] = spawn.z;
  const scale = spawn.scale ?? 1;
  Transform.scaleX[eid] = scale;
  Transform.scaleY[eid] = scale;
  Transform.scaleZ[eid] = scale;
  if (spawn.quaternion) {
    const [qx, qy, qz, qw] = spawn.quaternion;
    Transform.rotX[eid] = qx;
    Transform.rotY[eid] = qy;
    Transform.rotZ[eid] = qz;
    Transform.rotW[eid] = qw;
  } else if (spawn.yaw) {
    const half = spawn.yaw * 0.5;
    Transform.rotX[eid] = 0;
    Transform.rotY[eid] = Math.sin(half);
    Transform.rotZ[eid] = 0;
    Transform.rotW[eid] = Math.cos(half);
    Transform.eulerY[eid] = (spawn.yaw * 180) / Math.PI;
  }
  Transform.dirty[eid] = 1;

  if (spawn.cullDistance && spawn.cullDistance > 0) {
    state.addComponent(eid, DistanceCull);
    DistanceCull.maxDistance[eid] = spawn.cullDistance;
    DistanceCull.culled[eid] = 0;
  }

  setGltfUrl(state, eid, spawn.url);
  if (spawn.lod1Url) setInstancedLodUrl(state, eid, 1, spawn.lod1Url);
  if (spawn.lod2Url) setInstancedLodUrl(state, eid, 2, spawn.lod2Url);
  if (spawn.lodNear !== undefined) {
    setInstancedLodThreshold(state, eid, 1, spawn.lodNear);
  }
  if (spawn.lodMid !== undefined) {
    setInstancedLodThreshold(state, eid, 2, spawn.lodMid);
  }
  markGltfInstanced(state, eid);
  state.addComponent(eid, GltfPending);
  GltfPending.loaded[eid] = 0;
  if (spawn.name) state.setEntityName?.(spawn.name, eid);
  return eid;
}
