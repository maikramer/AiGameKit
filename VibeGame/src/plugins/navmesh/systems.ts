import * as THREE from 'three';
import { hasComponent } from 'bitecs';
import { defineQuery, type System } from '../../core';
import { Pathfinding } from 'three-pathfinding';
import { Transform, WorldTransform } from '../transforms';
import { NavAgent, NavMesh } from './components';
import { getNavmeshContext } from './context';

const navMeshQuery = defineQuery([NavMesh]);
const navAgentQuery = defineQuery([NavAgent, Transform]);

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
      if (NavMesh.loaded[eid]) continue;
      ensureZone(state);
      NavMesh.loaded[eid] = 1;
      break;
    }
    if (navMeshQuery(state.world).length === 0) {
      ensureZone(state);
    }
  },
};

export const NavAgentPathSystem: System = {
  group: 'simulation',
  update: (state) => {
    const ctx = getNavmeshContext(state);
    const pf = ctx.pathfinding;
    if (!pf || !ctx.zoneId) return;

    for (const eid of navAgentQuery(state.world)) {
      const st = NavAgent.status[eid];
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
        NavAgent.targetX[eid],
        NavAgent.targetY[eid],
        NavAgent.targetZ[eid]
      );

      const group = pf.getGroup(ctx.zoneId, start);
      if (group === null) {
        NavAgent.status[eid] = 3;
        continue;
      }

      const path = pf.findPath(start, end, ctx.zoneId, group);
      if (!path || path.length === 0) {
        if (start.distanceToSquared(end) < NavAgent.tolerance[eid] ** 2) {
          NavAgent.status[eid] = 2;
        } else {
          NavAgent.status[eid] = 3;
        }
        ctx.waypoints.delete(eid);
        ctx.waypointIndex.delete(eid);
        continue;
      }

      ctx.waypoints.set(eid, path);
      ctx.waypointIndex.set(eid, 0);
      NavAgent.status[eid] = 1;
    }
  },
};

export const NavAgentMoveSystem: System = {
  group: 'fixed',
  update: (state) => {
    const ctx = getNavmeshContext(state);
    const dt = state.time.fixedDeltaTime;

    for (const eid of navAgentQuery(state.world)) {
      if (NavAgent.status[eid] !== 1) continue;

      const path = ctx.waypoints.get(eid);
      if (!path || path.length === 0) {
        NavAgent.status[eid] = 2;
        continue;
      }

      let wi = ctx.waypointIndex.get(eid) ?? 0;
      if (wi >= path.length) {
        NavAgent.status[eid] = 2;
        continue;
      }

      const target = path[wi];
      const ox = Transform.posX[eid];
      const oy = Transform.posY[eid];
      const oz = Transform.posZ[eid];
      const pos = new THREE.Vector3(ox, oy, oz);
      const to = target.clone().sub(pos);
      const dist = to.length();
      const speed = NavAgent.speed[eid];
      const tol = NavAgent.tolerance[eid];

      if (dist < tol) {
        wi += 1;
        ctx.waypointIndex.set(eid, wi);
        if (wi >= path.length) {
          NavAgent.status[eid] = 2;
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
