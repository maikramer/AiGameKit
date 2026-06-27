import { logger } from '../../core/utils/logger';
import type {
  Crowd,
  CrowdAgent,
  NavMesh,
  NavMeshQuery,
} from 'recast-navigation';
import {
  Crowd as CrowdClass,
  NavMeshQuery as NavMeshQueryClass,
  init,
} from 'recast-navigation';
import { generateSoloNavMesh } from 'recast-navigation/generators';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { isLoadingEnforced, registerReadyGate } from '../../core/loading-gate';
import { Transform } from '../transforms/components';
import { Terrain } from '../terrain/components';
import { getTerrainContext } from '../terrain/utils';
import { NavMeshAgent, NavMeshSurface } from './components';
import { collectNavmeshGeometry, navmeshObstaclesLoaded } from './geometry';

const TERRAIN_GRACE_FRAMES = 30;
const MAX_INIT_WAIT_FRAMES = 600;
// Failsafe: once generation has been kicked off, never let the world-ready gate
// block on the navmesh longer than this. recast's wasm `init()` or the solo bake
// can hang or silently fail; without a ceiling the loading screen would wait on
// "navmesh…" forever and the game never starts. On timeout the gate releases in
// degraded mode (no crowd → enemies fall back to direct steering).
const NAVMESH_INIT_TIMEOUT_MS = 8000;

const AGENT_HEIGHT = 2.0;
const AGENT_RADIUS = 0.4;
// Extra walkable-area erosion beyond the agent radius. recast pushes the navmesh
// edge back by `walkableRadius` cells from every obstacle; agent radius alone
// (0.4 m = 1 cell) let enemies brush right against the house/tree/rock collider,
// and the kinematic controller then climbed the angled face (roof, trunk flare,
// boulder slope). Adding a margin keeps the path a clear standoff away so agents
// route AROUND props instead of grazing them. cs = 0.4 → each cell is 0.4 m.
const OBSTACLE_MARGIN = 0.4;
const MAX_STEP_HEIGHT = 0.4;
// Voxel cell size. Drives both navmesh fidelity and generation cost: the recast
// rasteriser allocates a (2·PLAY_AREA_RADIUS / cs)² column grid, so halving cs
// quadruples the work. 0.4 over a 240 m span = 600² grid — fine enough to carve
// 0.4 m-radius trunks while keeping generation sub-second.
const FIXED_CS = 0.4;
// Source-mesh resolution for the terrain collision surface. Independent of cs:
// recast re-voxelises at cs regardless, so a fine source mesh only wastes time.
// 180 over 240 m ≈ 1.3 m steps, plenty to capture the walkable slope.
const TERRAIN_SOURCE_DIVISIONS = 180;

const MAX_AGENTS = 256;
const MAX_AGENT_RADIUS = 0.6;

const PLAY_AREA_RADIUS = 120;

function navMeshConfig(worldSize: number) {
  void worldSize;
  const cs = FIXED_CS;
  return {
    cs,
    ch: cs,
    walkableSlopeAngle: 45,
    walkableHeight: Math.ceil(AGENT_HEIGHT / cs),
    walkableClimb: Math.max(1, Math.ceil(MAX_STEP_HEIGHT / cs)),
    walkableRadius: Math.max(
      1,
      Math.ceil((AGENT_RADIUS + OBSTACLE_MARGIN) / cs)
    ),
    maxVertsPerPoly: 6,
    detailSampleDist: cs * 6,
    detailSampleMaxError: cs,
  };
}

function navMeshReady(state: State): boolean {
  const rt = getNavMeshRuntime(state);
  if (rt.ready || rt.failed) return true;
  if (!rt.initStarted) {
    const surfaces = surfaceQuery(state.world);
    if (!surfaces.some((eid) => NavMeshSurface.enabled[eid] === 1)) {
      return true;
    }
    return false;
  }
  // Generation kicked off but never finished within the failsafe window — give
  // up holding the gate so a hung/slow bake can't deadlock the whole load.
  if (
    rt.initStartedAt > 0 &&
    performance.now() - rt.initStartedAt > NAVMESH_INIT_TIMEOUT_MS
  ) {
    rt.failed = true;
    logger.warn(
      '[NavMesh] Generation did not complete within ' +
        `${NAVMESH_INIT_TIMEOUT_MS}ms — releasing load gate (degraded steering).`
    );
    return true;
  }
  return false;
}

export interface NavMeshRuntime {
  initStarted: boolean;
  /** performance.now() when generateNavMesh was kicked off (0 = not yet). */
  initStartedAt: number;
  /** Generation gave up (empty geometry, error, or failsafe timeout). */
  failed: boolean;
  ready: boolean;
  graceFrames: number;
  navMesh: NavMesh | null;
  navMeshQuery: NavMeshQuery | null;
  crowd: Crowd | null;
  agents: Map<number, CrowdAgent>;
}

const stateToRuntime = new WeakMap<State, NavMeshRuntime>();
let activeRuntime: NavMeshRuntime | null = null;

export function getNavMeshRuntime(state: State): NavMeshRuntime {
  let rt = stateToRuntime.get(state);
  if (!rt) {
    rt = {
      initStarted: false,
      initStartedAt: 0,
      failed: false,
      ready: false,
      graceFrames: 0,
      navMesh: null,
      navMeshQuery: null,
      crowd: null,
      agents: new Map(),
    };
    stateToRuntime.set(state, rt);
    activeRuntime = rt;
  }
  return rt;
}

const surfaceQuery = defineQuery([NavMeshSurface]);
const agentQuery = defineQuery([NavMeshAgent]);

export const NavMeshInitSystem: System = {
  group: 'setup',
  setup(state) {
    registerReadyGate(state, 'navmesh', () => navMeshReady(state));
  },
  update(state: State) {
    if (state.headless) return;
    const rt = getNavMeshRuntime(state);
    if (rt.ready || rt.initStarted || rt.failed) return;

    const surfaces = surfaceQuery(state.world);
    const hasSurface = surfaces.some(
      (eid) => NavMeshSurface.enabled[eid] === 1
    );
    if (!hasSurface) return;

    const loading = isLoadingEnforced(state);

    const terrainCtx = getTerrainContext(state);
    let terrainReady = false;
    let worldSize = 200;
    for (const [eid, data] of terrainCtx) {
      if (data.initialized) {
        terrainReady = true;
        worldSize = Terrain.worldSize[eid];
        break;
      }
    }
    if (!terrainReady) return;

    rt.graceFrames++;
    if (rt.graceFrames < TERRAIN_GRACE_FRAMES) return;

    // Wait for obstacles to finish loading before baking, or they won't be
    // carved into the navmesh. Capped by MAX_INIT_WAIT_FRAMES so a stuck load
    // can't deadlock generation forever.
    if (rt.graceFrames < MAX_INIT_WAIT_FRAMES) {
      // The navmesh bakes from collision geometry; defer until every fixed
      // trimesh/convex obstacle's collision GLB has finished downloading.
      if (!navmeshObstaclesLoaded(state, PLAY_AREA_RADIUS)) return;
      if (loading) {
        const gltfPendingComp = state.getComponent('gltf-pending') as
          | { loaded: Uint8Array }
          | undefined;
        if (gltfPendingComp) {
          let pending = 0;
          for (let e = 0; e < gltfPendingComp.loaded.length; e++) {
            if (gltfPendingComp.loaded[e] === 0 && state.exists(e)) pending++;
          }
          if (pending > 0) return;
        }
      }
    }

    rt.initStarted = true;
    rt.initStartedAt = performance.now();
    void generateNavMesh(state, rt, worldSize);
  },
};

async function generateNavMesh(
  state: State,
  rt: NavMeshRuntime,
  worldSize: number
): Promise<void> {
  try {
    await init();

    const config = navMeshConfig(worldSize);
    const t0 = performance.now();

    const { positions, indices } = collectNavmeshGeometry(
      state,
      TERRAIN_SOURCE_DIVISIONS,
      PLAY_AREA_RADIUS
    );
    if (indices.length === 0) {
      logger.warn('[NavMesh] No geometry collected — skipping generation');
      rt.failed = true;
      return;
    }
    const tCollect = performance.now();

    const result = generateSoloNavMesh(positions, indices, config);
    if (!result.success) {
      logger.error('[NavMesh] Generation failed:', result.error);
      rt.failed = true;
      return;
    }
    const tGen = performance.now();

    const navMesh = result.navMesh;
    const navMeshQuery = new NavMeshQueryClass(navMesh);
    const crowd = new CrowdClass(navMesh, {
      maxAgents: MAX_AGENTS,
      maxAgentRadius: MAX_AGENT_RADIUS,
    });

    rt.navMesh = navMesh;
    rt.navMeshQuery = navMeshQuery;
    rt.crowd = crowd;
    rt.ready = true;

    logger.info(
      `[NavMesh] Generated (${indices.length / 3} tris, cs=${config.cs}) — ` +
        `collect ${(tCollect - t0).toFixed(0)}ms, ` +
        `recast ${(tGen - tCollect).toFixed(0)}ms`
    );
  } catch (err) {
    logger.error('[NavMesh] Generation error:', err);
    rt.failed = true;
  }
}

export const NavMeshAgentSystem: System = {
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;
    const rt = getNavMeshRuntime(state);
    if (!rt.ready || !rt.crowd) return;
    const crowd = rt.crowd;

    for (const eid of agentQuery(state.world)) {
      const existing = rt.agents.get(eid);

      if (existing) {
        if (NavMeshAgent.agentIndex[eid] === -1) {
          crowd.removeAgent(existing);
          rt.agents.delete(eid);
          continue;
        }
        if (NavMeshAgent.enabled[eid] === 0) continue;
        if (NavMeshAgent.hasTarget[eid] === 1) {
          existing.requestMoveTarget({
            x: NavMeshAgent.targetX[eid],
            y: NavMeshAgent.targetY[eid],
            z: NavMeshAgent.targetZ[eid],
          });
          NavMeshAgent.hasTarget[eid] = 0;
        }
        continue;
      }

      if (NavMeshAgent.enabled[eid] === 0) continue;

      const radius = NavMeshAgent.radius[eid] || 0.4;
      const height = NavMeshAgent.height[eid] || 1.0;
      const maxSpeed = NavMeshAgent.speed[eid] || 3.0;

      const pos = {
        x: Transform.posX[eid],
        y: Transform.posY[eid],
        z: Transform.posZ[eid],
      };

      const agent = crowd.addAgent(pos, {
        radius,
        height,
        maxAcceleration: 8.0,
        maxSpeed,
        collisionQueryRange: radius * 5,
        pathOptimizationRange: 2.0,
        separationWeight: 1.0,
      });

      NavMeshAgent.agentIndex[eid] = agent.agentIndex;
      rt.agents.set(eid, agent);

      state.onDestroy(eid, () => {
        const r = stateToRuntime.get(state);
        if (!r || !r.crowd) return;
        const a = r.agents.get(eid);
        if (a) {
          r.crowd.removeAgent(a);
          r.agents.delete(eid);
        }
      });
    }

    crowd.update(Math.min(state.time.deltaTime, 1 / 30));

    for (const [eid, agent] of rt.agents) {
      if (!state.exists(eid)) {
        crowd.removeAgent(agent);
        rt.agents.delete(eid);
        continue;
      }
      const p = agent.position();
      Transform.posX[eid] = p.x;
      Transform.posZ[eid] = p.z;

      const v = agent.velocity();
      const speed = Math.hypot(v.x, v.z);
      if (speed > 0.05) {
        Transform.eulerY[eid] = Math.atan2(v.x, v.z);
      }

      Transform.dirty[eid] = 1;
    }
  },
  dispose(state: State) {
    const rt = stateToRuntime.get(state);
    if (!rt) return;
    if (rt.crowd) {
      rt.crowd.destroy();
      rt.crowd = null;
    }
    rt.navMesh = null;
    rt.navMeshQuery = null;
    rt.agents.clear();
    rt.ready = false;
    rt.initStarted = false;
    if (activeRuntime === rt) activeRuntime = null;
  },
};

export function _getActiveRuntime(): NavMeshRuntime | null {
  return activeRuntime;
}

export { stateToRuntime };
