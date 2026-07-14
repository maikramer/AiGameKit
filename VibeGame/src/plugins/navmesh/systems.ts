import { logger } from '../../core/utils/logger';
import type {
  Crowd,
  CrowdAgent,
  NavMesh,
  NavMeshQuery,
} from 'recast-navigation';
import {
  Crowd as CrowdClass,
  importNavMesh,
  NavMeshQuery as NavMeshQueryClass,
  init,
} from 'recast-navigation';
import { defineQuery } from '../../core';
import type { State, System } from '../../core';
import { Transform } from '../transforms/components';
import { Terrain, TerrainPad } from '../terrain/components';
import { getTerrainContext, isTerrainDynamicsBlocking } from '../terrain/utils';
import { Road } from '../road/components';
import { Lake, River } from '../water/components';
import { bakeSoloNavMeshBytes } from './bake-worker';
import { NavMeshAgent, NavMeshSurface } from './components';
import {
  collectNavmeshGeometry,
  navmeshObstaclesLoaded,
  prefetchNavmeshObstacles,
} from './geometry';

// Short settle after terrain carvers latch `applied=1` — pads/roads/lakes/
// rivers already mutated the sampler + brush registry; a couple of frames
// cover late SOA writes.
const TERRAIN_GRACE_FRAMES = 2;
const MAX_INIT_WAIT_FRAMES = 600;

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
// Coarse source-mesh resolution for the play-area terrain skin. Dense patches
// over lake/river/road brush AABBs are added in buildAdaptiveTerrainGeometry.
// 96 over 240 m ≈ 2.5 m steps on the base grid (was 180 / ~1.3 m uniform).
const TERRAIN_SOURCE_DIVISIONS = 96;

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

/** Yield one animation frame so the loading overlay can fade and the first
 * gameplay frame can paint before the sync WASM bake stalls the main thread. */
function nextAnimationFrame(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => resolve());
    } else {
      setTimeout(resolve, 0);
    }
  });
}

export interface NavMeshRuntime {
  initStarted: boolean;
  /** performance.now() when generateNavMesh was kicked off (0 = not yet). */
  initStartedAt: number;
  /** Generation gave up (empty geometry or error). */
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
const padQuery = defineQuery([TerrainPad]);
const roadQuery = defineQuery([Road]);
const lakeQuery = defineQuery([Lake]);
const riverQuery = defineQuery([River]);

/**
 * Whether every system that mutates the terrain sampler before navmesh bake
 * has finished. The navmesh reads terrain heights from the shared sampler via
 * `buildTerrainGeometry`; if it bakes before the city pads, road corridors,
 * lakes and rivers carve their modifications into `sampler.data`, the baked
 * surface reflects the un-modified heightmap and agents sink below the final
 * terrain at those locations (e.g. at the city edge, over roads/rivers).
 *
 * Each apply system (TerrainPadApply / RoadApply / LakeApply / RiverApply)
 * latches `applied = 1` after mutating the sampler. Waiting on all of them
 * guarantees the navmesh samples the same surface the player walks on.
 */
function terrainCarversReady(state: State): boolean {
  // The sampler must be decoded (real heightmap, not the flat placeholder)
  // and collision-ready, matching the gate the apply systems use internally.
  if (isTerrainDynamicsBlocking(state)) return false;
  for (const [, data] of getTerrainContext(state)) {
    if (data.heightmapUrl && data.sampler.data === null) return false;
  }
  for (const eid of padQuery(state.world)) {
    if (TerrainPad.applied[eid] !== 1) return false;
  }
  for (const eid of roadQuery(state.world)) {
    if (Road.applied[eid] !== 1) return false;
  }
  for (const eid of lakeQuery(state.world)) {
    if (Lake.applied[eid] !== 1) return false;
  }
  for (const eid of riverQuery(state.world)) {
    if (River.applied[eid] !== 1) return false;
  }
  return true;
}

/**
 * Kicks off navmesh generation once terrain carvers and in-radius collision
 * obstacles are ready. Does NOT register a loading-gate entry — bake runs in
 * a Worker (with sync fallback) while AI uses direct steering until ready.
 */
export const NavMeshInitSystem: System = {
  group: 'setup',
  setup(state) {
    if (state.headless) return;
    // Overlap WASM download/compile with terrain decode + asset loads so the
    // bake itself is not paying cold-start cost on the critical path.
    void init().catch((err: unknown) => {
      logger.warn('[NavMesh] Early WASM init failed:', err);
    });
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

    // Start collision-GLB fetches as soon as obstacles exist — do not wait for
    // carvers/grace. First-touch lazy loads previously delayed the bake.
    prefetchNavmeshObstacles(state, PLAY_AREA_RADIUS);

    // Read the terrain field for worldSize, but gate generation on the full
    // carver pipeline (heightmap decoded + collision ready + every pad/road/
    // lake/river applied). Baking before the carvers mutate the sampler bakes
    // the un-modified heightmap — agents then sink below the final terrain at
    // flattened city pads, road corridors, lakes and rivers.
    let worldSize = 200;
    for (const [eid, data] of getTerrainContext(state)) {
      if (data.initialized) {
        worldSize = Terrain.worldSize[eid];
        break;
      }
    }
    if (!terrainCarversReady(state)) return;

    rt.graceFrames++;
    if (rt.graceFrames < TERRAIN_GRACE_FRAMES) return;

    // Wait for collision obstacles that carve the navmesh — not every visual
    // GLTF. Baking as soon as those are ready overlaps with remaining asset
    // loads so the loading screen rarely waits on navmesh. Capped so a stuck
    // collision download can't deadlock generation forever.
    if (rt.graceFrames < MAX_INIT_WAIT_FRAMES) {
      if (!navmeshObstaclesLoaded(state, PLAY_AREA_RADIUS)) return;
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
    // Main-thread WASM still needed for Crowd / NavMeshQuery / importNavMesh.
    await init();
    // Give the loading overlay a frame to fade before we spend main-thread
    // time on geometry collection (bake itself runs off-thread).
    await nextAnimationFrame();
    await nextAnimationFrame();

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
    const triCount = indices.length / 3;
    const tCollect = performance.now();

    // Off-thread bake (Worker); falls back to sync if Workers unavailable.
    // Geometry buffers may be transferred — do not reuse after this call.
    const bytes = await bakeSoloNavMeshBytes(positions, indices, config);
    const tGen = performance.now();

    const { navMesh } = importNavMesh(bytes);
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
      `[NavMesh] Generated (${triCount} tris, cs=${config.cs}) — ` +
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
        separationWeight: 2.5,
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

    if (rt.agents.size === 0) return;

    crowd.update(Math.min(state.time.deltaTime, 1 / 30));

    for (const [eid, agent] of rt.agents) {
      if (!state.exists(eid)) {
        crowd.removeAgent(agent);
        rt.agents.delete(eid);
        continue;
      }
      const p = agent.position();
      const prevX = Transform.posX[eid];
      const prevZ = Transform.posZ[eid];
      Transform.posX[eid] = p.x;
      Transform.posZ[eid] = p.z;

      const v = agent.velocity();
      const speed = Math.hypot(v.x, v.z);
      if (speed > 0.05) {
        Transform.eulerY[eid] = Math.atan2(v.x, v.z);
      }

      // Skip dirty when parked — avoids cascading WorldTransform recomputes.
      if (
        Math.abs(p.x - prevX) > 1e-4 ||
        Math.abs(p.z - prevZ) > 1e-4 ||
        speed > 0.05
      ) {
        Transform.dirty[eid] = 1;
      }
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
