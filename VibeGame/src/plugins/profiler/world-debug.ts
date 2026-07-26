/**
 * World debug snapshot for the profiler World tab: player pose, camera, nearby
 * entities. Tab text stays compact; JSON / `__VIBEGAME__.profiler.worldSnapshot()`
 * carries a rich `detail` payload per nearby entity.
 */
import { defineQuery, Parent, type State } from '../../core';
import {
  FACTION_TAG_NAMES,
  FactionComponent,
  getFaction,
  Health,
} from '../combat/components';
import { Destructible } from '../destructible/components';
import { getScriptFile } from '../entity-script/context';
import { MonoBehaviour } from '../entity-script/components';
import { getGltfLodUrls, getGltfUrl } from '../gltf-xml/context';
import { GltfLod, GltfPending } from '../gltf-xml/components';
import {
  BodyType,
  CharacterController,
  Collider,
  ColliderShape,
  Rigidbody,
} from '../physics/components';
import { getColliderMeshUrl } from '../physics/mesh-collider';
import { PlayerController } from '../player/components';
import { ThirdPersonCamera } from '../player-controller/components';
import { NavMeshAgent } from '../navmesh/components';
import {
  AI_MODE_ATTACK,
  AI_MODE_CHASE,
  AI_MODE_DEAD,
  AI_MODE_DETECT,
  AI_MODE_IDLE,
  AI_MODE_LUNGE,
  AiStateComponent,
} from '../rpg-ai/components';
import { ResourceNode } from '../rpg-resource-node/components';
import { getResourceNodeKind } from '../rpg-resource-node/utils';
import { DistanceCull, MainCamera } from '../rendering/components';
import { threeCameras } from '../rendering/utils';
import { SpawnVariation } from '../spawn-variation/components';
import { TerrainSpawned } from '../spawner/components';
import { getAabbPendingUrls } from '../spawner/bounds-context';
import { sampleTerrainSurface } from '../spawner/surface';
import { getGroundHeight } from '../terrain/height-sampler';
import { getTerrainHeightAt } from '../terrain/terrain-queries';
import { getTerrainContext } from '../terrain/utils';
import { boostAt } from '../terrain/density-map';
import { Transform, WorldTransform } from '../transforms/components';

const playerQuery = defineQuery([PlayerController, Transform]);
const cameraQuery = defineQuery([MainCamera, Transform]);
const tpcQuery = defineQuery([ThirdPersonCamera]);
const nearbyQuery = defineQuery([Transform]);
const parentQuery = defineQuery([Parent]);

export const DEFAULT_NEARBY_RADIUS = 30;
export const DEFAULT_NEARBY_LIMIT = 24;

/** Last State seen by the profiler panel (for bridge ``worldSnapshot()``). */
let boundState: State | null = null;

export function bindWorldDebugState(state: State): void {
  boundState = state;
}

/** Snapshot from the bound panel State, or null if profiler never refreshed. */
export function getBoundWorldDebugSnapshot(opts?: {
  nearbyRadius?: number;
  nearbyLimit?: number;
}): WorldDebugSnapshot | null {
  if (!boundState) return null;
  return getWorldDebugSnapshot(boundState, opts);
}

export interface WorldDebugVec3 {
  x: number;
  y: number;
  z: number;
}

export interface WorldDebugPlayer {
  eid: number;
  name: string;
  pos: WorldDebugVec3;
  worldPos: WorldDebugVec3;
  eulerYDeg: number;
  grounded: boolean | null;
  /** Analytic heightmap sample (may differ from visible mesh lattice). */
  terrainY: number | null;
  /** Density-aware mesh lattice + footprint (`getGroundHeight`). */
  groundY: number | null;
  deltaGroundY: number | null;
  densityBoost: number | null;
  vel: WorldDebugVec3 | null;
}

export interface WorldDebugCamera {
  eid: number;
  name: string;
  pos: WorldDebugVec3;
  worldPos: WorldDebugVec3;
  fov: number;
  near: number;
  far: number;
  threePos: WorldDebugVec3 | null;
  tpc: {
    eid: number;
    target: number;
    distance: number;
    height: number;
    yawDeg: number;
    pitchDeg: number;
    follow: WorldDebugVec3;
  } | null;
}

export type WorldDebugLabelSource =
  'name' | 'gltf' | 'script' | 'collider' | 'tag' | 'eid';

/** Rich per-entity diagnostics — JSON / bridge only; World tab ignores. */
export interface WorldDebugNearbyDetail {
  labelSource: WorldDebugLabelSource;
  worldPos: WorldDebugVec3;
  eulerDeg: WorldDebugVec3;
  scale: WorldDebugVec3;
  terrainY: number | null;
  groundY: number | null;
  surfaceY: number | null;
  deltaGroundY: number | null;
  densityBoost: number | null;
  parent: { eid: number; name: string } | null;
  children: Array<{
    eid: number;
    name: string;
    gltfUrl: string | null;
    script: string | null;
  }>;
  gltfUrl: string | null;
  lodUrls: readonly [string, string, string] | null;
  gltfPending: boolean | null;
  gltfLodLevel: number | null;
  script: string | null;
  monoReady: boolean | null;
  health: { current: number; max: number } | null;
  faction: string | null;
  ai: {
    mode: string;
    modeId: number;
    target: number;
    cooldown: number;
    leash: number;
  } | null;
  destructible: {
    hits: number;
    hitsTaken: number;
    range: number;
    breakStyle: number;
  } | null;
  terrainSpawned: {
    yOffset: number;
    halfWidth: number;
    alignToTerrain: boolean;
    aabbPending: boolean;
    surfaceEpsilon: number;
    scaleY: number;
    normalY: number;
    aabbPendingUrl: string | null;
  } | null;
  distanceCull: { maxDistance: number; culled: boolean } | null;
  navAgent: boolean;
  rigidbody: {
    type: string;
    mass: number;
    gravityScale: number;
    vel: WorldDebugVec3;
  } | null;
  collider: {
    shape: string;
    meshUrl: string | null;
    isSensor: boolean;
    size: WorldDebugVec3;
    radius: number;
    height: number;
  } | null;
  resource: {
    kind: string;
    yield: number;
    depleted: boolean;
    respawn: number;
  } | null;
  variation: {
    color: WorldDebugVec3;
    brightness: number;
    contrast: number;
  } | null;
  components: string[];
}

export interface WorldDebugNearby {
  eid: number;
  name: string;
  pos: WorldDebugVec3;
  dist: number;
  tags: string[];
  /** Full diagnostic payload for JSON / `worldSnapshot()`. */
  detail: WorldDebugNearbyDetail;
}

export interface WorldDebugSnapshot {
  t: number;
  frame: number;
  nearbyRadius: number;
  nearbyLimit: number;
  /** Entities inside radius before limit trim (interesting only). */
  nearbyInRadius: number;
  origin: WorldDebugVec3;
  player: WorldDebugPlayer | null;
  camera: WorldDebugCamera | null;
  nearby: WorldDebugNearby[];
  entityCount: number;
}

const AI_MODE_NAMES: Record<number, string> = {
  [AI_MODE_IDLE]: 'idle',
  [AI_MODE_DETECT]: 'detect',
  [AI_MODE_CHASE]: 'chase',
  [AI_MODE_ATTACK]: 'attack',
  [AI_MODE_LUNGE]: 'lunge',
  [AI_MODE_DEAD]: 'dead',
};

const BODY_TYPE_NAMES: Record<number, string> = {
  [BodyType.Dynamic]: 'dynamic',
  [BodyType.Fixed]: 'fixed',
  [BodyType.KinematicPositionBased]: 'kinematic-pos',
  [BodyType.KinematicVelocityBased]: 'kinematic-vel',
};

const COLLIDER_SHAPE_NAMES: Record<number, string> = {
  [ColliderShape.Box]: 'box',
  [ColliderShape.Sphere]: 'sphere',
  [ColliderShape.Capsule]: 'capsule',
  [ColliderShape.TriMesh]: 'trimesh',
  [ColliderShape.ConvexHull]: 'convex-hull',
};

function vec3(x: number, y: number, z: number): WorldDebugVec3 {
  return { x, y, z };
}

function fmt(n: number, digits = 2): string {
  if (!Number.isFinite(n)) return 'nan';
  return n.toFixed(digits);
}

/** Basename without extension: `/assets/meshes/cactus_lod0.glb` → `cactus_lod0`. */
export function assetStem(url: string): string {
  const trimmed = url.trim();
  if (!trimmed) return '';
  const base = trimmed.split(/[\\/]/).pop() ?? trimmed;
  return base.replace(/\.[^.]+$/, '') || base;
}

function safeTerrainY(state: State, x: number, z: number): number | null {
  try {
    const h = getTerrainHeightAt(state, x, z);
    return Number.isFinite(h) ? h : null;
  } catch {
    return null;
  }
}

function safeGroundY(state: State, x: number, z: number): number | null {
  try {
    const h = getGroundHeight(state, x, z);
    return Number.isFinite(h) ? h : null;
  } catch {
    return null;
  }
}

function safeSurfaceY(state: State, x: number, z: number): number | null {
  try {
    const s = sampleTerrainSurface(state, x, z, 0.75);
    return s && Number.isFinite(s.worldY) ? s.worldY : null;
  } catch {
    return null;
  }
}

function densityBoostAt(state: State, wx: number, wz: number): number | null {
  const context = getTerrainContext(state);
  for (const [, data] of context) {
    if (!data.initialized || !data.density) continue;
    const localX = wx - data.worldOffset.x;
    const localZ = wz - data.worldOffset.z;
    return boostAt(data.density, localX, localZ);
  }
  return null;
}

function buildChildrenByParent(state: State): Map<number, number[]> {
  const map = new Map<number, number[]>();
  for (const child of parentQuery(state.world)) {
    const p = Parent.entity[child];
    if (p < 0) continue;
    let list = map.get(p);
    if (!list) {
      list = [];
      map.set(p, list);
    }
    list.push(child);
  }
  return map;
}

function resolveVisualGltfUrl(
  state: State,
  eid: number,
  children: number[]
): string | null {
  const direct = getGltfUrl(state, eid);
  if (direct) return direct;
  for (const c of children) {
    const u = getGltfUrl(state, c);
    if (u) return u;
  }
  const pending = getAabbPendingUrls(state).get(eid);
  return pending ?? null;
}

function collectTags(state: State, eid: number): string[] {
  const tags: string[] = [];
  if (state.hasComponent(eid, PlayerController)) tags.push('player');
  if (state.hasComponent(eid, MainCamera)) tags.push('camera');
  if (state.hasComponent(eid, ThirdPersonCamera)) tags.push('tpc');
  if (state.hasComponent(eid, Health)) tags.push('health');
  if (state.hasComponent(eid, Destructible)) tags.push('destructible');
  if (state.hasComponent(eid, CharacterController)) tags.push('cct');
  if (state.hasComponent(eid, TerrainSpawned)) tags.push('terrain-spawned');
  if (state.hasComponent(eid, NavMeshAgent)) tags.push('nav');
  if (state.hasComponent(eid, DistanceCull)) tags.push('cull');
  if (state.hasComponent(eid, AiStateComponent)) tags.push('ai');
  if (state.hasComponent(eid, FactionComponent)) tags.push('faction');
  if (state.hasComponent(eid, ResourceNode)) tags.push('resource');
  if (state.hasComponent(eid, SpawnVariation)) tags.push('variation');
  if (state.hasComponent(eid, MonoBehaviour)) tags.push('script');
  if (state.hasComponent(eid, Rigidbody)) tags.push('rigidbody');
  if (state.hasComponent(eid, Collider)) tags.push('collider');
  if (getGltfUrl(state, eid)) tags.push('gltf');
  return tags;
}

export function resolveEntityLabel(
  state: State,
  eid: number,
  children: number[] = []
): { name: string; source: WorldDebugLabelSource } {
  const named = state.getEntityName(eid)?.trim();
  if (named) return { name: named, source: 'name' };

  const gltf = resolveVisualGltfUrl(state, eid, children);
  if (gltf) {
    const stem = assetStem(gltf);
    if (stem) return { name: stem, source: 'gltf' };
  }

  const script = getScriptFile(state, eid);
  if (script) {
    const stem = assetStem(script);
    if (stem) return { name: stem, source: 'script' };
  }

  if (state.hasComponent(eid, Collider)) {
    const meshUrl = getColliderMeshUrl(state, eid);
    if (meshUrl) {
      const stem = assetStem(meshUrl);
      if (stem) return { name: stem, source: 'collider' };
    }
  }

  if (state.hasComponent(eid, FactionComponent)) {
    const id = FactionComponent.tag[eid] ?? 0;
    const faction = FACTION_TAG_NAMES[id] ?? getFaction(state, eid);
    if (faction) return { name: faction, source: 'tag' };
  }

  const tags = collectTags(state, eid);
  const preferred = tags.find(
    (t) =>
      t === 'destructible' ||
      t === 'health' ||
      t === 'terrain-spawned' ||
      t === 'ai' ||
      t === 'resource'
  );
  if (preferred) return { name: `${preferred}:${eid}`, source: 'tag' };

  return { name: `#${eid}`, source: 'eid' };
}

function isInterestingNearby(
  state: State,
  eid: number,
  children: number[],
  tags: string[]
): boolean {
  if (state.getEntityName(eid)?.trim()) return true;
  if (tags.length > 0) return true;
  if (resolveVisualGltfUrl(state, eid, children)) return true;
  if (getScriptFile(state, eid)) return true;
  return false;
}

function buildNearbyDetail(
  state: State,
  eid: number,
  childrenByParent: Map<number, number[]>,
  labelSource: WorldDebugLabelSource
): WorldDebugNearbyDetail {
  const children = childrenByParent.get(eid) ?? [];
  const px = Transform.posX[eid];
  const py = Transform.posY[eid];
  const pz = Transform.posZ[eid];
  const groundY = safeGroundY(state, px, pz);
  const terrainY = safeTerrainY(state, px, pz);
  const surfaceY = safeSurfaceY(state, px, pz);

  let parent: WorldDebugNearbyDetail['parent'] = null;
  if (state.hasComponent(eid, Parent)) {
    const pe = Parent.entity[eid];
    if (pe >= 0 && state.exists(pe)) {
      const pl = resolveEntityLabel(state, pe, childrenByParent.get(pe) ?? []);
      parent = { eid: pe, name: pl.name };
    }
  }

  const childInfos = children.map((c) => {
    const cl = resolveEntityLabel(state, c, childrenByParent.get(c) ?? []);
    return {
      eid: c,
      name: cl.name,
      gltfUrl: getGltfUrl(state, c) ?? null,
      script: getScriptFile(state, c) ?? null,
    };
  });

  const gltfUrl = resolveVisualGltfUrl(state, eid, children);
  const lodOnSelf = getGltfLodUrls(state, eid);
  let lodUrls = lodOnSelf ?? null;
  if (!lodUrls) {
    for (const c of children) {
      const lod = getGltfLodUrls(state, c);
      if (lod) {
        lodUrls = lod;
        break;
      }
    }
  }

  const components = collectTags(state, eid);

  return {
    labelSource,
    worldPos: state.hasComponent(eid, WorldTransform)
      ? vec3(
          WorldTransform.posX[eid],
          WorldTransform.posY[eid],
          WorldTransform.posZ[eid]
        )
      : vec3(px, py, pz),
    eulerDeg: vec3(
      (Transform.eulerX[eid] * 180) / Math.PI,
      (Transform.eulerY[eid] * 180) / Math.PI,
      (Transform.eulerZ[eid] * 180) / Math.PI
    ),
    scale: vec3(
      Transform.scaleX[eid],
      Transform.scaleY[eid],
      Transform.scaleZ[eid]
    ),
    terrainY,
    groundY,
    surfaceY,
    deltaGroundY: groundY !== null ? py - groundY : null,
    densityBoost: densityBoostAt(state, px, pz),
    parent,
    children: childInfos,
    gltfUrl,
    lodUrls,
    gltfPending: state.hasComponent(eid, GltfPending)
      ? GltfPending.loaded[eid] !== 1
      : children.some((c) => state.hasComponent(c, GltfPending))
        ? children.some(
            (c) =>
              state.hasComponent(c, GltfPending) && GltfPending.loaded[c] !== 1
          )
        : null,
    gltfLodLevel: state.hasComponent(eid, GltfLod)
      ? GltfLod.activeLevel[eid]
      : children.find((c) => state.hasComponent(c, GltfLod)) !== undefined
        ? GltfLod.activeLevel[
            children.find((c) => state.hasComponent(c, GltfLod))!
          ]
        : null,
    script: getScriptFile(state, eid) ?? null,
    monoReady: state.hasComponent(eid, MonoBehaviour)
      ? MonoBehaviour.ready[eid] === 1
      : null,
    health: state.hasComponent(eid, Health)
      ? { current: Health.current[eid], max: Health.max[eid] }
      : null,
    faction: state.hasComponent(eid, FactionComponent)
      ? getFaction(state, eid)
      : null,
    ai: state.hasComponent(eid, AiStateComponent)
      ? {
          mode:
            AI_MODE_NAMES[AiStateComponent.mode[eid]] ??
            `mode_${AiStateComponent.mode[eid]}`,
          modeId: AiStateComponent.mode[eid],
          target: AiStateComponent.target[eid],
          cooldown: AiStateComponent.cooldown[eid],
          leash: AiStateComponent.leash[eid],
        }
      : null,
    destructible: state.hasComponent(eid, Destructible)
      ? {
          hits: Destructible.hits[eid],
          hitsTaken: Destructible.hitsTaken[eid],
          range: Destructible.range[eid],
          breakStyle: Destructible.breakStyle[eid],
        }
      : null,
    terrainSpawned: state.hasComponent(eid, TerrainSpawned)
      ? {
          yOffset: TerrainSpawned.yOffset[eid],
          halfWidth: TerrainSpawned.halfWidth[eid],
          alignToTerrain: TerrainSpawned.alignToTerrain[eid] === 1,
          aabbPending: TerrainSpawned.aabbPending[eid] === 1,
          surfaceEpsilon: TerrainSpawned.surfaceEpsilon[eid],
          scaleY: TerrainSpawned.scaleY[eid],
          normalY: TerrainSpawned.normalY[eid],
          aabbPendingUrl: getAabbPendingUrls(state).get(eid) ?? null,
        }
      : null,
    distanceCull: state.hasComponent(eid, DistanceCull)
      ? {
          maxDistance: DistanceCull.maxDistance[eid],
          culled: DistanceCull.culled[eid] === 1,
        }
      : null,
    navAgent: state.hasComponent(eid, NavMeshAgent),
    rigidbody: state.hasComponent(eid, Rigidbody)
      ? {
          type:
            BODY_TYPE_NAMES[Rigidbody.type[eid]] ??
            `type_${Rigidbody.type[eid]}`,
          mass: Rigidbody.mass[eid],
          gravityScale: Rigidbody.gravityScale[eid],
          vel: vec3(
            Rigidbody.velX[eid],
            Rigidbody.velY[eid],
            Rigidbody.velZ[eid]
          ),
        }
      : null,
    collider: state.hasComponent(eid, Collider)
      ? {
          shape:
            COLLIDER_SHAPE_NAMES[Collider.shape[eid]] ??
            `shape_${Collider.shape[eid]}`,
          meshUrl: getColliderMeshUrl(state, eid) ?? null,
          isSensor: Collider.isSensor[eid] === 1,
          size: vec3(
            Collider.sizeX[eid],
            Collider.sizeY[eid],
            Collider.sizeZ[eid]
          ),
          radius: Collider.radius[eid],
          height: Collider.height[eid],
        }
      : null,
    resource: state.hasComponent(eid, ResourceNode)
      ? {
          kind: getResourceNodeKind(state, eid),
          yield: ResourceNode.yield[eid],
          depleted: ResourceNode.depleted[eid] === 1,
          respawn: ResourceNode.respawn[eid],
        }
      : null,
    variation: state.hasComponent(eid, SpawnVariation)
      ? {
          color: vec3(
            SpawnVariation.colorR[eid],
            SpawnVariation.colorG[eid],
            SpawnVariation.colorB[eid]
          ),
          brightness: SpawnVariation.brightness[eid],
          contrast: SpawnVariation.contrast[eid],
        }
      : null,
    components,
  };
}

function resolvePlayerEid(state: State): number | null {
  const named = state.getEntityByName('hero');
  if (named !== null && state.hasComponent(named, Transform)) return named;
  const players = playerQuery(state.world);
  return players.length > 0 ? players[0]! : null;
}

export function getWorldDebugSnapshot(
  state: State,
  opts?: { nearbyRadius?: number; nearbyLimit?: number }
): WorldDebugSnapshot {
  const nearbyRadius = opts?.nearbyRadius ?? DEFAULT_NEARBY_RADIUS;
  const nearbyLimit = opts?.nearbyLimit ?? DEFAULT_NEARBY_LIMIT;
  const radiusSq = nearbyRadius * nearbyRadius;
  const childrenByParent = buildChildrenByParent(state);

  let player: WorldDebugPlayer | null = null;
  const playerEid = resolvePlayerEid(state);
  if (playerEid !== null) {
    const px = Transform.posX[playerEid];
    const py = Transform.posY[playerEid];
    const pz = Transform.posZ[playerEid];
    const hasWorld = state.hasComponent(playerEid, WorldTransform);
    const hasCct = state.hasComponent(playerEid, CharacterController);
    const terrainY = safeTerrainY(state, px, pz);
    const groundY = safeGroundY(state, px, pz);
    player = {
      eid: playerEid,
      name: state.getEntityName(playerEid) || 'player',
      pos: vec3(px, py, pz),
      worldPos: hasWorld
        ? vec3(
            WorldTransform.posX[playerEid],
            WorldTransform.posY[playerEid],
            WorldTransform.posZ[playerEid]
          )
        : vec3(px, py, pz),
      eulerYDeg: (Transform.eulerY[playerEid] * 180) / Math.PI,
      grounded: hasCct ? CharacterController.grounded[playerEid] === 1 : null,
      terrainY,
      groundY,
      deltaGroundY: groundY !== null ? py - groundY : null,
      densityBoost: densityBoostAt(state, px, pz),
      vel: state.hasComponent(playerEid, Rigidbody)
        ? vec3(
            Rigidbody.velX[playerEid],
            Rigidbody.velY[playerEid],
            Rigidbody.velZ[playerEid]
          )
        : null,
    };
  }

  let camera: WorldDebugCamera | null = null;
  const cams = cameraQuery(state.world);
  if (cams.length > 0) {
    const eid = cams[0]!;
    const three = threeCameras.get(eid);
    const tpcs = tpcQuery(state.world);
    const tpcEid = tpcs.length > 0 ? tpcs[0]! : null;
    camera = {
      eid,
      name: state.getEntityName(eid) || 'main-camera',
      pos: vec3(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]),
      worldPos: state.hasComponent(eid, WorldTransform)
        ? vec3(
            WorldTransform.posX[eid],
            WorldTransform.posY[eid],
            WorldTransform.posZ[eid]
          )
        : vec3(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]),
      fov: MainCamera.fov[eid],
      near: MainCamera.near[eid],
      far: MainCamera.far[eid],
      threePos: three
        ? vec3(three.position.x, three.position.y, three.position.z)
        : null,
      tpc:
        tpcEid !== null
          ? {
              eid: tpcEid,
              target: ThirdPersonCamera.target[tpcEid]!,
              distance: ThirdPersonCamera.distance[tpcEid]!,
              height: ThirdPersonCamera.height[tpcEid]!,
              yawDeg: (ThirdPersonCamera.yaw[tpcEid]! * 180) / Math.PI,
              pitchDeg: (ThirdPersonCamera.pitch[tpcEid]! * 180) / Math.PI,
              follow: vec3(
                ThirdPersonCamera.followX[tpcEid]!,
                ThirdPersonCamera.followY[tpcEid]!,
                ThirdPersonCamera.followZ[tpcEid]!
              ),
            }
          : null,
    };
  }

  const origin = player?.pos ?? camera?.pos ?? vec3(0, 0, 0);
  const nearby: WorldDebugNearby[] = [];
  const all = nearbyQuery(state.world);
  for (const eid of all) {
    if (player && eid === player.eid) continue;
    if (camera && eid === camera.eid) continue;
    const dx = Transform.posX[eid] - origin.x;
    const dy = Transform.posY[eid] - origin.y;
    const dz = Transform.posZ[eid] - origin.z;
    const d2 = dx * dx + dy * dy + dz * dz;
    if (d2 > radiusSq) continue;
    const children = childrenByParent.get(eid) ?? [];
    const tags = collectTags(state, eid);
    if (!isInterestingNearby(state, eid, children, tags)) continue;
    const label = resolveEntityLabel(state, eid, children);
    nearby.push({
      eid,
      name: label.name,
      pos: vec3(Transform.posX[eid], Transform.posY[eid], Transform.posZ[eid]),
      dist: Math.sqrt(d2),
      tags,
      detail: buildNearbyDetail(state, eid, childrenByParent, label.source),
    });
  }
  nearby.sort((a, b) => a.dist - b.dist);
  const nearbyInRadius = nearby.length;
  if (nearby.length > nearbyLimit) nearby.length = nearbyLimit;

  return {
    t: performance.now(),
    frame: state.time.frameCount,
    nearbyRadius,
    nearbyLimit,
    nearbyInRadius,
    origin,
    player,
    camera,
    nearby,
    entityCount: all.length,
  };
}

/** Plain-text body for the profiler World pane (compact; no `detail`). */
export function renderWorldTab(snap: WorldDebugSnapshot): string {
  const lines: string[] = [];
  lines.push(
    `frame=${snap.frame}  entities=${snap.entityCount}  nearby=${snap.nearby.length}/${snap.nearbyInRadius} ≤${snap.nearbyRadius}m`
  );
  lines.push('');

  if (!snap.player) {
    lines.push('Player: (none — no PlayerController / hero)');
  } else {
    const p = snap.player;
    lines.push(`Player  ${p.name}  eid=${p.eid}`);
    lines.push(`  pos       ${fmt(p.pos.x)}  ${fmt(p.pos.y)}  ${fmt(p.pos.z)}`);
    lines.push(
      `  world     ${fmt(p.worldPos.x)}  ${fmt(p.worldPos.y)}  ${fmt(p.worldPos.z)}`
    );
    lines.push(
      `  yaw       ${fmt(p.eulerYDeg, 1)}°` +
        (p.grounded === null ? '' : `  grounded=${p.grounded ? 'yes' : 'no'}`) +
        (p.groundY !== null
          ? `  groundY=${fmt(p.groundY)}  Δy=${fmt(p.deltaGroundY ?? 0)}`
          : p.terrainY !== null
            ? `  terrainY=${fmt(p.terrainY)}  Δy=${fmt(p.pos.y - p.terrainY)}`
            : '')
    );
    if (p.densityBoost !== null) {
      lines.push(`  density   boost=${p.densityBoost}`);
    }
    if (p.vel) {
      lines.push(
        `  vel       ${fmt(p.vel.x)}  ${fmt(p.vel.y)}  ${fmt(p.vel.z)}`
      );
    }
  }

  lines.push('');
  if (!snap.camera) {
    lines.push('Camera: (none — no MainCamera)');
  } else {
    const c = snap.camera;
    lines.push(`Camera  ${c.name}  eid=${c.eid}`);
    lines.push(`  pos       ${fmt(c.pos.x)}  ${fmt(c.pos.y)}  ${fmt(c.pos.z)}`);
    lines.push(
      `  world     ${fmt(c.worldPos.x)}  ${fmt(c.worldPos.y)}  ${fmt(c.worldPos.z)}`
    );
    if (c.threePos) {
      lines.push(
        `  three.js  ${fmt(c.threePos.x)}  ${fmt(c.threePos.y)}  ${fmt(c.threePos.z)}`
      );
    }
    lines.push(
      `  lens      fov=${fmt(c.fov, 1)}  near=${fmt(c.near, 2)}  far=${fmt(c.far, 0)}`
    );
    if (c.tpc) {
      const t = c.tpc;
      lines.push(
        `  TPC       target=${t.target}  dist=${fmt(t.distance)}  height=${fmt(t.height)}`
      );
      lines.push(
        `            yaw=${fmt(t.yawDeg, 1)}°  pitch=${fmt(t.pitchDeg, 1)}°`
      );
      lines.push(
        `            follow ${fmt(t.follow.x)}  ${fmt(t.follow.y)}  ${fmt(t.follow.z)}`
      );
    }
  }

  lines.push('');
  lines.push(`Nearby (closest first, r=${snap.nearbyRadius}m):`);
  if (snap.nearby.length === 0) {
    lines.push('  (none with name/tags/gltf/script in range)');
  } else {
    for (const n of snap.nearby) {
      const tags = n.tags.length ? ` [${n.tags.join(',')}]` : '';
      const src =
        n.detail && n.detail.labelSource !== 'name'
          ? ` ⟨${n.detail.labelSource}⟩`
          : '';
      lines.push(
        `  ${fmt(n.dist, 1).padStart(5)}m  ${n.name.padEnd(28).slice(0, 28)}  eid=${String(n.eid).padEnd(5)}  ${fmt(n.pos.x)} ${fmt(n.pos.y)} ${fmt(n.pos.z)}${tags}${src}`
      );
    }
  }

  lines.push('');
  lines.push(
    'Tip: ?profiler=world  ·  __VIBEGAME__.profiler.worldSnapshot()  (JSON tem detail.*)'
  );
  return lines.join('\n');
}
