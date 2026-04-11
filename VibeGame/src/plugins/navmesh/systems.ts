import * as THREE from 'three';
import { hasComponent } from 'bitecs';
import { defineQuery, type System } from '../../core';
import { Pathfinding } from 'three-pathfinding';
import { Collider } from '../physics';
import { getRenderingContext } from '../rendering';
import { Transform, WorldTransform } from '../transforms';
import { NavMeshAgent, NavMeshSurface } from './components';
import { getNavmeshContext } from './context';

const navMeshQuery = defineQuery([NavMeshSurface]);
const navAgentQuery = defineQuery([NavMeshAgent, Transform]);
const colliderQuery = defineQuery([Collider]);

function ensureZone(state: import('../../core').State): void {
  const ctx = getNavmeshContext(state);
  if (ctx.zoneRegistered) return;
  const geom = new THREE.PlaneGeometry(40, 40, 4, 4);
  geom.rotateX(-Math.PI / 2);
  const zone = Pathfinding.createZone(geom);
  ctx.pathfinding.setZoneData(ctx.zoneId, zone);
  ctx.zoneRegistered = true;
}

/** Regista uma zona navmesh por omissão (plano) na primeira entidade `nav-mesh`. */
export const NavMeshLoadSystem: System = {
  group: 'setup',
  update: (state) => {
    for (const eid of navMeshQuery(state.world)) {
      if (NavMeshSurface.loaded[eid]) continue;
      ensureZone(state);
      NavMeshSurface.loaded[eid] = 1;
      break;
    }
    if (navMeshQuery(state.world).length === 0) {
      ensureZone(state);
    }
  },
};

function collectCollidableGeometry(
  state: import('../../core').State
): THREE.BufferGeometry | null {
  const ctx = getRenderingContext(state);
  const geometries: THREE.BufferGeometry[] = [];
  const world = state.world;

  for (const eid of colliderQuery(world)) {
    const instance = ctx.entityInstances.get(eid);
    if (!instance) continue;

    const pool = instance.unlit
      ? ctx.unlitMeshPools.get(instance.poolId)
      : ctx.meshPools.get(instance.poolId);
    if (!pool) continue;

    const mesh = pool as THREE.InstancedMesh;
    const geom = mesh.geometry.clone();

    const mtx = new THREE.Matrix4();
    mesh.getMatrixAt(instance.instanceId, mtx);
    geom.applyMatrix4(mtx);

    geometries.push(geom);
  }

  if (geometries.length === 0) return null;

  const merged = mergeGeometriesManual(geometries);

  for (const g of geometries) g.dispose();
  return merged;
}

function mergeGeometriesManual(
  geometries: THREE.BufferGeometry[]
): THREE.BufferGeometry {
  let totalVerts = 0;
  for (const g of geometries) {
    totalVerts += g.attributes.position.count;
  }

  const positions = new Float32Array(totalVerts * 3);
  const indices: number[] = [];
  let vertOffset = 0;
  let idxOffset = 0;

  for (const g of geometries) {
    const pos = g.attributes.position;
    for (let i = 0; i < pos.count; i++) {
      positions[(vertOffset + i) * 3] = pos.getX(i);
      positions[(vertOffset + i) * 3 + 1] = pos.getY(i);
      positions[(vertOffset + i) * 3 + 2] = pos.getZ(i);
    }
    if (g.index) {
      for (let i = 0; i < g.index.count; i++) {
        indices[idxOffset++] = g.index.getX(i) + vertOffset;
      }
    }
    vertOffset += pos.count;
  }

  const merged = new THREE.BufferGeometry();
  merged.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  if (indices.length > 0) {
    merged.setIndex(indices);
  }
  return merged;
}

export const NavMeshBuildSystem: System = {
  group: 'setup',
  update: (state) => {
    const ctx = getNavmeshContext(state);
    if (ctx.zoneRegistered) return;

    let needsSceneBuild = false;
    for (const eid of navMeshQuery(state.world)) {
      if (NavMeshSurface.buildFromScene[eid] === 1) {
        needsSceneBuild = true;
        break;
      }
    }

    if (!needsSceneBuild) return;

    const geom = collectCollidableGeometry(state);
    if (!geom) return;

    const zone = Pathfinding.createZone(geom);
    ctx.pathfinding.setZoneData(ctx.zoneId, zone);
    ctx.zoneRegistered = true;
    geom.dispose();
  },
};

export const NavAgentPathSystem: System = {
  group: 'simulation',
  update: (state) => {
    const ctx = getNavmeshContext(state);
    const pf = ctx.pathfinding;
    if (!pf || !ctx.zoneId) return;

    for (const eid of navAgentQuery(state.world)) {
      const st = NavMeshAgent.status[eid];
      if (st === 1 || st === 2) continue;

      const ox = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posX[eid]
        : Transform.posX[eid];
      const oy = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posY[eid]
        : Transform.posY[eid];
      const oz = hasComponent(state.world, WorldTransform, eid)
        ? WorldTransform.posZ[eid]
        : Transform.posZ[eid];

      const start = new THREE.Vector3(ox, oy, oz);
      const end = new THREE.Vector3(
        NavMeshAgent.targetX[eid],
        NavMeshAgent.targetY[eid],
        NavMeshAgent.targetZ[eid]
      );

      const group = pf.getGroup(ctx.zoneId, start);
      if (group === null) {
        NavMeshAgent.status[eid] = 3;
        continue;
      }

      const path = pf.findPath(start, end, ctx.zoneId, group);
      if (!path || path.length === 0) {
        if (start.distanceToSquared(end) < NavMeshAgent.tolerance[eid] ** 2) {
          NavMeshAgent.status[eid] = 2;
        } else {
          NavMeshAgent.status[eid] = 3;
        }
        ctx.waypoints.delete(eid);
        ctx.waypointIndex.delete(eid);
        continue;
      }

      ctx.waypoints.set(eid, path);
      ctx.waypointIndex.set(eid, 0);
      NavMeshAgent.status[eid] = 1;
    }
  },
};

export const NavAgentMoveSystem: System = {
  group: 'fixed',
  update: (state) => {
    const ctx = getNavmeshContext(state);
    const dt = state.time.fixedDeltaTime;

    for (const eid of navAgentQuery(state.world)) {
      if (NavMeshAgent.status[eid] !== 1) continue;

      const path = ctx.waypoints.get(eid);
      if (!path || path.length === 0) {
        NavMeshAgent.status[eid] = 2;
        continue;
      }

      let wi = ctx.waypointIndex.get(eid) ?? 0;
      if (wi >= path.length) {
        NavMeshAgent.status[eid] = 2;
        continue;
      }

      const target = path[wi];
      const ox = Transform.posX[eid];
      const oy = Transform.posY[eid];
      const oz = Transform.posZ[eid];
      const pos = new THREE.Vector3(ox, oy, oz);
      const to = target.clone().sub(pos);
      const dist = to.length();
      const speed = NavMeshAgent.speed[eid];
      const tol = NavMeshAgent.tolerance[eid];

      if (dist < tol) {
        wi += 1;
        ctx.waypointIndex.set(eid, wi);
        if (wi >= path.length) {
          NavMeshAgent.status[eid] = 2;
        }
        continue;
      }

      to.normalize();
      const step = Math.min(dist, speed * dt);
      Transform.posX[eid] += to.x * step;
      Transform.posY[eid] += to.y * step;
      Transform.posZ[eid] += to.z * step;

      if (hasComponent(state.world, WorldTransform, eid)) {
        WorldTransform.posX[eid] = Transform.posX[eid];
        WorldTransform.posY[eid] = Transform.posY[eid];
        WorldTransform.posZ[eid] = Transform.posZ[eid];
      }
    }
  },
};
