# BVH Plugin

Provides a unified mesh BVH index (via `three-mesh-bvh`) for fast ray and
shape queries against static world geometry. Consumed by:

- Character grounding (physics)
- ThirdPersonCamera (wall avoidance, future)
- Raycast plugin (mode 1)
- Spawner placement (optional, slope tests)
- Future picking, AI line-of-sight

Rapier still owns dynamic collision. The BVH is purely a read-only spatial
acceleration structure over static meshes.

## What it indexes

| Source | Geometry | Layer |
|--------|----------|-------|
| Terrain (`TerrainPlugin`) | Displaced plane built from heightmap, 256 segments | `0x0001` |
| GLTF roots with `Rigidbody.type === Fixed` or no rigidbody | Baked world-space triangles | `0x0002` |
| Future: `MeshRenderer` primitives | (not yet wired) | `0x0004` |

## API

```ts
import { castBvhRay, getBvhSurfaceHeight } from 'vibegame/bvh';

const hit = castBvhRay(state, origin, dir, maxDist, layerMask);
const groundY = getBvhSurfaceHeight(state, x, y + 5, z, 10);
```

## Plugin order

`BvhPlugin` must be registered **after** `TerrainPlugin`, `GltfXmlPlugin`,
`PhysicsPlugin` and `RenderingPlugin` (its sync systems pull data from those
contexts).

## Costs

- Build: ~50 ms for a 10 km × 256 segment terrain (one-time).
- Static GLTF bake: linear in triangle count, runs once per entity.
- Raycast: O(log n) per mesh; for the simple-rpg scene (terrain + a few GLTFs)
  a downward ground probe is ~5 µs on modern hardware.
