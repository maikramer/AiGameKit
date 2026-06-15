# NavMesh Plugin

Navigation mesh generation and Crowd-based agent pathfinding using [`recast-navigation`](https://github.com/isaac-mason/recast-navigation-js).

## Overview

Generates a Solo NavMesh from terrain + static GLB obstacles at runtime, then provides Crowd-based pathfinding for AI entities. Agents avoid obstacles, walk on walkable surfaces, and steer around each other.

## Usage

### Declarative (XML)

Place `<NavMesh>` in the scene to trigger generation. It waits for terrain initialization + a grace period (120 frames) for GLBs to load, then generates.

```html
<Scene>
  <Terrain heightmap-url="/assets/heightmap.png" size="256"></Terrain>
  <NavMesh></NavMesh>

  <GLTFLoader model-url="/assets/models/tree.glb" pos="5 0 -3"></GLTFLoader>

  <!-- Agent: add nav-mesh-agent to any entity with a Transform -->
  <GameObject pos="0 5 0">
    <NavMeshAgent speed="3" radius="0.4" height="1"></NavMeshAgent>
  </GameObject>
</Scene>
```

### Programmatic API

```ts
import {
  isNavMeshReady,
  createAgent,
  setAgentTarget,
  clearAgentTarget,
  removeAgent,
  getAgentPosition,
  getNavMeshDebugMesh,
} from 'vibegame/navmesh';

if (isNavMeshReady()) {
  const idx = createAgent(state, eid, { speed: 3, radius: 0.4, height: 1 });
  setAgentTarget(state, eid, 10, 0, 5);
}

const debugMesh = await getNavMeshDebugMesh();
scene.add(debugMesh);
```

## Components

| Component           | Fields                                                                  | Description                              |
| ------------------- | ----------------------------------------------------------------------- | ---------------------------------------- |
| `nav-mesh-surface`  | `enabled`, `generated`                                                  | Flag: presence triggers navmesh build    |
| `nav-mesh-agent`    | `agentIndex`, `speed`, `radius`, `height`, `targetX/Y/Z`, `hasTarget`, `enabled` | Agent data; `agentIndex=-1` means unregistered |

## Systems

| System              | Group       | Description                                                          |
| ------------------- | ----------- | ------------------------------------------------------------------- |
| `NavMeshInitSystem` | `setup`     | Waits for terrain + GLBs, runs `init()`, generates navmesh, creates Crowd |
| `NavMeshAgentSystem`| `simulation`| Creates/removes Crowd agents, applies targets, syncs position/heading to Transform |

## Recipes

| Recipe          | Element         | Description                              |
| --------------- | --------------- | --------------------------------------- |
| `navMeshRecipe` | `<NavMesh>`     | Adds `nav-mesh-surface` flag component   |
| `navMeshAgentRecipe` | `<NavMeshAgent>` | Merge recipe: adds `nav-mesh-agent` to parent entity |

## NavMesh Config

Fixed cell size `cs = 0.4` over a `PLAY_AREA_RADIUS = 120` (240 m span) → a 600²
recast column grid. The grid scales as `(2·radius / cs)²`, so cs is the dominant
generation cost — keep it as large as obstacle fidelity allows. Walkable params
derive from agent dimensions and cs.

Terrain **source** mesh resolution is decoupled from cs (`TERRAIN_SOURCE_DIVISIONS
= 180`): recast re-voxelises at cs regardless, so a finer source mesh only wastes
collection time. (Previously divisions = `bounds·2/cs` ≈ 800 → a 1.28 M-triangle
source + 640 k grid that took seconds.)

Agent defaults: height=2.0m, radius=0.4m, max step=0.4m, max slope=45°.
Crowd: `maxAgents: 256, maxAgentRadius: 0.6`.
Generation logs collect + recast timings to the console.

## Geometry Collection

Two passes, merged into one indexed mesh:

1. **Terrain** — a `TERRAIN_SOURCE_DIVISIONS²` grid sampled from the heightmap
   (`sampleHeightAt`).
2. **Collider obstacles** — every fixed (or rigidbody-less), non-sensor
   `Box`/`TriMesh`/`ConvexHull` collider within bounds. This is the **single source
   of truth**: the navmesh carves holes exactly where physics blocks the player,
   regardless of which render path drew the prop (instanced vegetation, auto-instance
   pool, or individual GLTF clone). Trimesh geometry comes from the collision-GLB
   cache (`physics/mesh-collider.ts` — `requestColliderMesh` + `buildMeshColliderGeometry`,
   the same data Rapier uses); boxes are emitted from `Collider.size*` + `posOffset`.
   Each is transformed by the rigidbody's world pose (`obstacleWorldMatrix`).

Why colliders, not visual meshes: trunks/rocks have vertical sides (slope > 45°) so
recast marks them non-walkable → clean holes. It also sidesteps the old visual-bake
pitfalls (3 separate instancing systems to chase; a world-space height cull that
deleted every obstacle because the terrain sits at y≈15-20).

Init waits for terrain init + a grace period + `navmeshObstaclesLoaded()` (all fixed
trimesh/convex collision GLBs downloaded), capped by `MAX_INIT_WAIT_FRAMES`, so
late-loading obstacles are present in the bake.

## Dependencies

- `recast-navigation` (WASM) — core NavMesh/Crowd/Query
- `@recast-navigation/three` — NavMeshHelper for debugging

Vite requires `optimizeDeps: { exclude: ['recast-navigation'] }` (already configured in `vite.config.ts`).
