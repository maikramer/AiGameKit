# VibeGame — Plugin Physics MVP Rewrite (Dynamic Body + Velocity)

**Date:** 2026-05-10
**Status:** Approved
**Scope:** VibeGame physics plugin (`src/plugins/physics/`)
**Driver:** Fix `recursive use of an object detected` RefCell borrow bug in Rapier WASM

## 1. Problem

The current physics plugin (`src/plugins/physics/`) is 1550+ lines across two files (`utils.ts`, `systems.ts`) mixing character controller logic, force/impulse application, kinematic movement, teleportation, collision events, interpolation, and platform detection. The `KinematicCharacterController.computeColliderMovement()` + `RigidBody.setNextKinematicTranslation()` pattern triggers a wasm-bindgen `RefCell` double-borrow whenever the character touches the ground.

Root cause: `computeColliderMovement` internally borrows `RawRigidBodySet` through the controller (shared with the `RigidBody` handle). Calling `setNextKinematicTranslation` on the same body afterwards attempts a second mutable borrow on the same `RefCell`.

## 2. Solution

**Abordagem B — Dynamic RigidBody controlled by velocity**

Instead of a kinematic character controller, the player is a **dynamic rigid body** with locked rotations. Movement is applied via `setLinvel()`, gravity is handled by the physics world, collision with terrain is automatic.

This eliminates the `KinematicCharacterController` entirely and avoids the `RefCell` borrow pattern that caused the crash.

## 3. Package Upgrade

| From | To |
|------|-----|
| `@dimforge/rapier3d-compat@0.18.2` | `@dimforge/rapier3d-simd@0.19.3` |

All import paths updated:
- `src/plugins/physics/*`
- `src/plugins/joints/*`
- `src/plugins/raycast/*`
- `src/plugins/terrain/*`
- `src/plugins/water/*`
- `src/vite/index.ts` (alias)
- `vite.config.ts` + `vite.config.cdn.ts` (external/bundle)

## 4. New File Layout

```
src/plugins/physics/
├── context.md          # updated
├── plugin.ts           # minimal registration
├── index.ts            # public exports
├── components.ts       # Rigidbody, Collider only
├── systems.ts          # 5 systems (init, move, jump, step, sync)
├── recipes.ts          # rigidbody + collider recipes
├── world.ts            # RAPIER.World singleton (lazy init, no PhysicsWorld component)
└── body.ts             # createRigidBody, createCollider, bodyFromEntity helpers
```

### Files deleted (no longer needed)

- `utils.ts` (logic split into `body.ts` + `world.ts`)
- Previous 14+ systems collapsed into 5

## 5. Components

| Component | Fields | Purpose |
|-----------|--------|---------|
| `Rigidbody` | `posX/Y/Z`, `rotX/Y/Z/W`, `velX/Y/Z`, `type`, `mass`, `gravityScale`, `lockRotX/Y/Z` | Cache of physics state + initialization params |
| `Collider` | `shape`, `sizeX/Y/Z`, `radius`, `height`, `friction`, `restitution`, `density`, `sensor`, `membershipGroups`, `filterGroups`, `posOffsetX/Y/Z`, `rotOffsetX/Y/Z/W` | Shape + material data |

### Components removed

`PhysicsWorld`, `CharacterController`, `CharacterMovement`, `InterpolatedTransform`, `CollisionEvents`, `TouchedEvent`, `TouchEndedEvent`, `KinematicMove`, `KinematicRotate`, `SetLinearVelocity`, `SetAngularVelocity`, `ApplyForce`, `ApplyImpulse`, `ApplyTorque`, `ApplyAngularImpulse`.

## 6. Systems (5 systems, all in `fixed` group)

| # | System | After | Action |
|---|--------|-------|--------|
| 1 | `PhysicsInitSystem` | — | Lazy-create `RAPIER.World`. Create `RigidBody` + `Collider` for new entities. Store in `Map<eid, RigidBody>` |
| 2 | `ApplyMovementSystem` | `PhysicsInitSystem` | Read input components (`inputX`, `inputZ`). If grounded → `setLinvel(forward * speedX, 0, forward * speedZ)`. Read `grounded` from raycast (component) |
| 3 | `ApplyJumpSystem` | `ApplyMovementSystem` | If `jumpFlag === 1` and `grounded === 1` → `applyImpulse(0, jumpForce, 0)`. Clear jumpFlag |
| 4 | `PhysicsStepSystem` | `ApplyJumpSystem` | `world.step(dt)` |
| 5 | `PhysicsSyncSystem` | `PhysicsStepSystem` | `Rigidbody.posX/Y/Z = body.translation()`. `Rigidbody.rotX/Y/Z/W = body.rotation()`. `Rigidbody.velX/Y/Z = body.linvel()` |

### Systems removed

`PhysicsWorldSystem`, `PhysicsInitializationSystem`, `PhysicsCleanupSystem`, `CharacterMovementSystem`, `CollisionEventCleanupSystem`, `ApplyForcesSystem`, `ApplyTorquesSystem`, `ApplyImpulsesSystem`, `ApplyAngularImpulsesSystem`, `SetVelocitySystem`, `TeleportationSystem`, `PhysicsRapierSyncSystem`, `PhysicsInterpolationSystem`, `KinematicMovementSystem`.

## 7. Data Flow Per Frame (Fixed)

```
Input plugin (or player plugin) writes MoveInput.x/Z, JumpInput.trigger, Grounded.grounded
    ↓
ApplyMovementSystem reads MoveInput + Grounded, sets body.linvel()
    ↓
ApplyJumpSystem reads JumpInput, if grounded → body.applyImpulse(up)
    ↓
PhysicsStepSystem: world.step(dt)
    ↓
PhysicsSyncSystem: read body.translation/rotation/linvel → Rigidbody components
    ↓
Rendering uses Rigidbody.pos/rot directly (no InterpolatedTransform in MVP)
```

**Note:** `MoveInput`, `JumpInput`, and `Grounded` are lightweight input helper components. They may be defined in the player plugin or in `src/plugins/physics/components.ts` as temporary MVP helpers. Their definition is implementation detail.

## 8. Recipes

### RigidBody

```html
<Rigidbody type="dynamic" mass="70" gravityScale="1" rotW="1" />
```

| Attribute | Default | Description |
|-----------|---------|-------------|
| `type` | `dynamic` | Only `dynamic` supported in MVP |
| `mass` | `1` | Body mass (kg) |
| `gravityScale` | `1` | Multiplier on world gravity |
| `posX/Y/Z` | `0` | Initial position |
| `rotX/Y/Z` | `0` | Initial rotation (Euler) |
| `rotW` | `1` | Quaternion W component |

### Collider

```html
<Collider shape="capsule" radius="0.3" height="1.6" friction="0.5" density="1" />
```

| Attribute | Default | Description |
|-----------|---------|-------------|
| `shape` | `box` | `box`, `sphere`, `capsule` |
| `sizeX/Y/Z` | `1` | Box half-extents (×2 for full size) |
| `radius` | `0.5` | Sphere radius / capsule radius |
| `height` | `1` | Capsule height |
| `friction` | `0.5` | Surface friction |
| `density` | `1` | Mass density (if mass not set) |

## 9. Character Controller — Manual (No AutoFeatures)

Grounded detection is done via **raycast** straight down from entity center (not via Rapier `CharacterController`):

- Ray origin: `Rigidbody.pos + (0, capsuleRadius + epsilon, 0)`
- Ray direction: `(0, -1, 0)`
- Max distance: `groundCheckDistance` (e.g. `0.15`)
- If ray hits collider → `grounded = 1`, else `0`

**No slope sliding, no auto-step, no snap-to-ground, no moving platforms** in MVP.

## 10. Modified Files

| File | Change |
|------|--------|
| `VibeGame/package.json` | Replace `@dimforge/rapier3d-compat` dependency with `@dimforge/rapier3d-simd@0.19.3` |
| `VibeGame/vite.config.ts` | Update external/alias for `rapier3d-simd` |
| `VibeGame/vite.config.cdn.ts` | Update CDN alias |
| `src/vite/index.ts` | Update import alias |
| `src/plugins/joints/*` | Update import paths (types only, no logic change in MVP) |
| `src/plugins/raycast/*` | Update import paths |
| `src/plugins/terrain/*` | Update import paths |
| `src/plugins/water/*` | Update import paths |
| `src/plugins/physics/components.ts` | Rewrite — only Rigidbody + Collider |
| `src/plugins/physics/systems.ts` | Rewrite — 5 systems |
| `src/plugins/physics/plugin.ts` | Rewrite — register 2 components + 5 systems + 2 recipes |
| `src/plugins/physics/index.ts` | Rewrite exports |
| `src/plugins/physics/recipes.ts` | Rewrite — Rigidbody + Collider recipes only |

## 11. Out of Scope (MVP — add later)

- `CharacterController` auto-step
- Slope sliding / max slope angle
- Snap-to-ground
- Platform parenting / moving platforms
- `InterpolatedTransform` (inter-frame smoothing) — renderer interpolates directly
- Force/torque/impulse generic components
- Collision events (start/end, touched)
- Joints
- Kinematic bodies (position/velocity-based)
- `SetLinearVelocity` / `SetAngularVelocity` components
- Teleportation
- `PhysicsInterpolationSystem`
- `CollisionEvents`, `TouchedEvent`, `TouchEndedEvent`
- `castShape` / `castCollider` beyond simple raycast for ground check

## 12. Constraints

- **No backward compatibility** — this is a breaking change. All existing physics XML in examples (`simple-rpg`, `hello-world`) must be updated.
- **No `bpy` or `trimesh`** — terrain collider uses Rapier `Heightfield` directly from heightmap PNG.
- **MVP is 5 fixed-group systems only** — no `setup`, `simulation`, or `draw` group physics systems.
- **Renderer reads from `Rigidbody` directly** — no separate interpolation component in MVP.
- **All `RAPIER.Vector3`/`Quaternion` objects freed immediately** — use plain numbers between calls, allocate Rapier objects only at the FFI boundary.

## 13. Success Criteria

1. `simple-rpg` example runs without `recursive use` error.
2. Character walks on terrain heightfield without falling through.
3. Character jumps and lands on terrain.
4. `bun run check` passes (no TypeScript errors in physics plugin).
5. `bun run build` succeeds with `@dimforge/rapier3d-simd`.
6. Bundle size does not regress significantly (SIMD build expected to be similar to compat).
