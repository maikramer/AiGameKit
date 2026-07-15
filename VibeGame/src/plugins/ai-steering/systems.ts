import * as THREE from 'three';
import { defineQuery, type State, type System } from '../../core';
import { BodyType, Collider, Rigidbody } from '../physics/components';
import { getBodyForEntity } from '../physics/systems';
import { Transform, WorldTransform } from '../transforms';
import { SteeringAgent, SteeringTarget } from './components';
import { getSteeringMap, type SteeringRow } from './context';
import { SteeringVehicle, type ObstacleLike } from './vehicle';

const steerQuery = defineQuery([SteeringAgent, SteeringTarget, Transform]);
const obstacleQuery = defineQuery([Rigidbody, Collider, Transform]);
const _obstacleCacheByState = new WeakMap<State, ObstacleLike[]>();
/** Rebuild fixed-obstacle list when count changes or every N frames (safety). */
const _obstacleMetaByState = new WeakMap<
  State,
  { count: number; frame: number }
>();
const OBSTACLE_REBUILD_INTERVAL = 45;

function getObstacleCache(state: State): ObstacleLike[] {
  let cache = _obstacleCacheByState.get(state);
  if (!cache) {
    cache = [];
    _obstacleCacheByState.set(state, cache);
  }
  return cache;
}

function rebuildObstacleCache(state: State, cache: ObstacleLike[]): number {
  let obstacleCount = 0;
  for (const eid of obstacleQuery(state.world)) {
    if (Rigidbody.type[eid] !== BodyType.Fixed) continue;
    let ge = cache[obstacleCount];
    if (!ge) {
      ge = { position: new THREE.Vector3(), boundingRadius: 0 };
      cache[obstacleCount] = ge;
    }
    ge.position.set(
      Transform.posX[eid],
      Transform.posY[eid],
      Transform.posZ[eid]
    );
    const r = Collider.radius[eid];
    ge.boundingRadius =
      r > 0
        ? r
        : Math.max(
            Collider.sizeX[eid],
            Collider.sizeY[eid],
            Collider.sizeZ[eid]
          ) / 2;
    obstacleCount++;
  }
  cache.length = obstacleCount;
  return obstacleCount;
}

function ensureVehicle(state: State, eid: number): SteeringRow {
  const map = getSteeringMap(state);
  let row = map.get(eid);
  if (row) return row;

  const vehicle = new SteeringVehicle();
  vehicle.seekActive = true;
  vehicle.fleeActive = false;
  vehicle.wanderActive = false;
  vehicle.obstacleActive = true;
  vehicle.obstacleWeight = 1.5;

  row = { vehicle };
  map.set(eid, row);
  return row;
}

function syncFromEcs(eid: number, row: SteeringRow): void {
  const v = row.vehicle;
  v.position.x = Transform.posX[eid];
  v.position.y = Transform.posY[eid];
  v.position.z = Transform.posZ[eid];
}

function syncTarget(state: State, eid: number, row: SteeringRow): void {
  const te = SteeringTarget.targetEntity[eid];
  let tx = SteeringTarget.targetX[eid];
  let ty = SteeringTarget.targetY[eid];
  let tz = SteeringTarget.targetZ[eid];
  if (te > 0 && state.exists(te) && state.hasComponent(te, Transform)) {
    tx = Transform.posX[te];
    ty = Transform.posY[te];
    tz = Transform.posZ[te];
  }
  row.vehicle.seekTarget.set(tx, ty, tz);
  row.vehicle.fleeTarget.set(tx, ty, tz);
}

function applyBehavior(eid: number, row: SteeringRow): void {
  const b = SteeringAgent.behavior[eid];
  row.vehicle.seekActive = b === 0;
  row.vehicle.wanderActive = b === 1;
  row.vehicle.fleeActive = b === 2;
}

export const SteeringSyncSystem: System = {
  group: 'simulation',
  update: (state) => {
    const dt = state.time.deltaTime || 1 / 60;
    const _obstacleCache = getObstacleCache(state);
    const frame = state.time.frameCount;
    const meta = _obstacleMetaByState.get(state);
    // Fixed obstacles rarely move — rebuild on an interval (not every frame).
    if (!meta || frame - meta.frame >= OBSTACLE_REBUILD_INTERVAL) {
      const n = rebuildObstacleCache(state, _obstacleCache);
      _obstacleMetaByState.set(state, { count: n, frame });
    }

    for (const eid of steerQuery(state.world)) {
      if (!SteeringAgent.active[eid]) continue;

      const row = ensureVehicle(state, eid);
      row.vehicle.maxSpeed = SteeringAgent.maxSpeed[eid];
      row.vehicle.maxForce = SteeringAgent.maxForce[eid];
      const groundY = Transform.posY[eid];
      syncFromEcs(eid, row);
      syncTarget(state, eid, row);
      applyBehavior(eid, row);
      row.vehicle.obstacles = _obstacleCache;

      row.vehicle.update(dt);

      // Steering is planar: Y is owned externally (terrain snap / placement),
      // not the steerer. Seek/wander operate in 3D and would otherwise let the
      // agent drift up or sink into the ground.
      row.vehicle.position.y = groundY;
      row.vehicle.velocity.y = 0;

      const body = getBodyForEntity(state, eid);
      const rtype = Rigidbody.type[eid];
      const isDynamic = !!body && rtype === BodyType.Dynamic;

      // For dynamic bodies the physics step owns the Transform — writing it here
      // (and below) would fight the body and cause jitter. Only drive the
      // Transform directly for kinematic / body-less agents.
      if (!isDynamic) {
        Transform.posX[eid] = row.vehicle.position.x;
        Transform.posY[eid] = row.vehicle.position.y;
        Transform.posZ[eid] = row.vehicle.position.z;
        Transform.rotX[eid] = row.vehicle.rotation.x;
        Transform.rotY[eid] = row.vehicle.rotation.y;
        Transform.rotZ[eid] = row.vehicle.rotation.z;
        Transform.rotW[eid] = row.vehicle.rotation.w;
        Transform.dirty[eid] = 1;
      }

      if (body) {
        if (rtype === BodyType.KinematicPositionBased) {
          body.setNextKinematicTranslation({
            x: row.vehicle.position.x,
            y: row.vehicle.position.y,
            z: row.vehicle.position.z,
          });
          body.setNextKinematicRotation({
            x: row.vehicle.rotation.x,
            y: row.vehicle.rotation.y,
            z: row.vehicle.rotation.z,
            w: row.vehicle.rotation.w,
          });
        } else if (isDynamic) {
          const vel = row.vehicle.velocity;
          body.setLinvel({ x: vel.x, y: 0, z: vel.z }, true);
        }
      }

      if (!isDynamic && state.hasComponent(eid, WorldTransform)) {
        WorldTransform.posX[eid] = Transform.posX[eid];
        WorldTransform.posY[eid] = Transform.posY[eid];
        WorldTransform.posZ[eid] = Transform.posZ[eid];
        WorldTransform.rotX[eid] = Transform.rotX[eid];
        WorldTransform.rotY[eid] = Transform.rotY[eid];
        WorldTransform.rotZ[eid] = Transform.rotZ[eid];
        WorldTransform.rotW[eid] = Transform.rotW[eid];
      }
    }

    // Drop steering rows whose entities were destroyed, so the per-state map
    // doesn't leak across waves/levels.
    const map = getSteeringMap(state);
    if (map.size > 0) {
      for (const key of map.keys()) {
        if (!state.exists(key)) map.delete(key);
      }
    }
  },
};
