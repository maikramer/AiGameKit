# VibeGame Physics Plugin MVP Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite VibeGame physics plugin → 2 components + 5 systems + dynamic body + velocity control. Migrate `@dimforge/rapier3d-compat` → `@dimforge/rapier3d-simd@0.19.3`.

**Architecture:** Dynamic rigid body with locked rotations. Movement via `setLinvel()`, jump via `applyImpulse()`. Manual ground detection via raycast. No `KinematicCharacterController`.

**Tech Stack:** TypeScript 5.6, bun, `@dimforge/rapier3d-simd@0.19.3`, bitECS

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/plugins/physics/components.ts` | Rewrite | `Rigidbody` + `Collider` (2 components) |
| `src/plugins/physics/body.ts` | Create | `createBody()`, `createCollider()`, body↔entity mapping |
| `src/plugins/physics/world.ts` | Create | Lazy `RAPIER.World` singleton, init logic |
| `src/plugins/physics/systems.ts` | Rewrite | 5 systems: Init, Move, Jump, Step, Sync |
| `src/plugins/physics/recipes.ts` | Rewrite | `rigidbody` + `collider` recipes only |
| `src/plugins/physics/plugin.ts` | Rewrite | Register 2 components + 5 systems + 2 recipes |
| `src/plugins/physics/index.ts` | Rewrite | Public exports |
| `src/plugins/physics/context.md` | Rewrite | Updated documentation |
| `src/plugins/joints/<anything>` | Modify | Update imports to `rapier3d-simd` |
| `src/plugins/raycast/<anything>` | Modify | Update imports to `rapier3d-simd` |
| `src/plugins/terrain/<anything>` | Modify | Update import type |
| `src/plugins/water/<anything>` | Modify | Update import type |
| `src/vite/index.ts` | Modify | Alias `@dimforge/rapier3d` → `rapier3d-simd` |
| `vite.config.ts` | Modify | External `rapier3d-simd` instead of `rapier3d-compat` |
| `vite.config.cdn.ts` | Modify | Same |
| `package.json` | Modify | Dependency swap |
| `src/plugins/physics/utils.ts` | Delete | Dead code |

---

## Task 1: Update package.json and install dependency

**Files:**
- Modify: `VibeGame/package.json`
- Run: `bun install`

- [ ] **Step 1: Swap dependency**

  Edit `VibeGame/package.json` line `182`:
  
  ```json
  "@dimforge/rapier3d-simd": "^0.19.3"
  ```
  
  Remove: `"@dimforge/rapier3d-compat": "^0.18.2"`

- [ ] **Step 2: Install new dependency**

  ```bash
  cd VibeGame && bun install
  ```
  
  Expected: `@dimforge/rapier3d-simd` installed in `node_modules/`, lockfile updated.

- [ ] **Step 3: Commit**

  ```bash
  git add VibeGame/package.json VibeGame/bun.lock
  git commit -m "deps: migrate @dimforge/rapier3d-compat → @dimforge/rapier3d-simd@0.19.3"
  ```

---

## Task 2: Rewrite src/plugins/physics/components.ts

**Files:**
- Create: `src/plugins/physics/components.ts`

- [ ] **Step 1: Rewrite with only Rigidbody + Collider**

  ```typescript
  import { Types } from 'bitecs';
  import { defineComponent } from '../../core';

  export const BodyType = {
    Dynamic: 0,
    Fixed: 1,
  } as const;

  export const ColliderShape = {
    Box: 0,
    Sphere: 1,
    Capsule: 2,
  } as const;

  export const Rigidbody = defineComponent({
    type: Types.ui8,
    mass: Types.f32,
    gravityScale: Types.f32,
    lockRotX: Types.ui8,
    lockRotY: Types.ui8,
    lockRotZ: Types.ui8,

    posX: Types.f32,
    posY: Types.f32,
    posZ: Types.f32,
    rotX: Types.f32,
    rotY: Types.f32,
    rotZ: Types.f32,
    rotW: Types.f32,
    eulerX: Types.f32,
    eulerY: Types.f32,
    eulerZ: Types.f32,

    velX: Types.f32,
    velY: Types.f32,
    velZ: Types.f32,
  });

  export const Collider = defineComponent({
    shape: Types.ui8,
    sizeX: Types.f32,
    sizeY: Types.f32,
    sizeZ: Types.f32,
    radius: Types.f32,
    height: Types.f32,
    friction: Types.f32,
    restitution: Types.f32,
    density: Types.f32,
    sensor: Types.ui8,
    membershipGroups: Types.ui16,
    filterGroups: Types.ui16,
    posOffsetX: Types.f32,
    posOffsetY: Types.f32,
    posOffsetZ: Types.f32,
    rotOffsetX: Types.f32,
    rotOffsetY: Types.f32,
    rotOffsetZ: Types.f32,
    rotOffsetW: Types.f32,
  });
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/components.ts
  git commit -m "feat(physics): rewrite components — Rigidbody + Collider only"
  ```

---

## Task 3: Create src/plugins/physics/world.ts

**Files:**
- Create: `src/plugins/physics/world.ts`

- [ ] **Step 1: Create world singleton**

  ```typescript
  import * as RAPIER from '@dimforge/rapier3d-simd';

  const GRAVITY_Y = -60;
  const TIMESTEP = 1 / 50;

  let world: RAPIER.World | null = null;
  let initialized = false;

  export async function initPhysics(): Promise<void> {
    if (initialized) return;
    await RAPIER.init();
    initialized = true;
  }

  export function getOrCreateWorld(): RAPIER.World {
    if (!world) {
      world = new RAPIER.World(new RAPIER.Vector3(0, GRAVITY_Y, 0));
      world.timestep = TIMESTEP;
    }
    return world;
  }

  export function getWorld(): RAPIER.World | null {
    return world;
  }

  export function stepWorld(): void {
    if (world) {
      world.step();
    }
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/world.ts
  git commit -m "feat(physics): create world.ts — lazy singleton with init/step"
  ```

---

## Task 4: Create src/plugins/physics/body.ts

**Files:**
- Create: `src/plugins/physics/body.ts`

- [ ] **Step 1: Create body helpers**

  ```typescript
  import * as RAPIER from '@dimforge/rapier3d-simd';
  import { Rigidbody, Collider, BodyType, ColliderShape } from './components';

  export function createRapierBody(entity: number): RAPIER.RigidBodyDesc {
    const type = Rigidbody.type[entity] ?? BodyType.Dynamic;

    let desc: RAPIER.RigidBodyDesc;
    switch (type) {
      case BodyType.Fixed:
        desc = RAPIER.RigidBodyDesc.fixed();
        break;
      case BodyType.Dynamic:
      default:
        desc = RAPIER.RigidBodyDesc.dynamic();
        break;
    }

    desc.setTranslation(
      Rigidbody.posX[entity] ?? 0,
      Rigidbody.posY[entity] ?? 0,
      Rigidbody.posZ[entity] ?? 0
    );

    const mass = Math.max(0.001, Rigidbody.mass[entity] || 1);
    desc.setAdditionalMass(mass, true);

    const gravityScale = Rigidbody.gravityScale[entity] ?? 1;
    desc.setGravityScale(gravityScale, true);

    if (
      Rigidbody.lockRotX[entity] ||
      Rigidbody.lockRotY[entity] ||
      Rigidbody.lockRotZ[entity]
    ) {
      desc.setEnabledRotations(
        !Rigidbody.lockRotX[entity],
        !Rigidbody.lockRotY[entity],
        !Rigidbody.lockRotZ[entity],
        true
      );
    }

    return desc;
  }

  export function createRapierColliderDesc(entity: number): RAPIER.ColliderDesc {
    const shape = Collider.shape[entity] ?? ColliderShape.Box;

    let desc: RAPIER.ColliderDesc;
    switch (shape) {
      case ColliderShape.Sphere:
        desc = RAPIER.ColliderDesc.ball(Collider.radius[entity] || 0.5);
        break;
      case ColliderShape.Capsule:
        desc = RAPIER.ColliderDesc.capsule(
          (Collider.height[entity] || 1) / 2,
          Collider.radius[entity] || 0.5
        );
        break;
      case ColliderShape.Box:
      default:
        desc = RAPIER.ColliderDesc.cuboid(
          (Collider.sizeX[entity] || 1) / 2,
          (Collider.sizeY[entity] || 1) / 2,
          (Collider.sizeZ[entity] || 1) / 2
        );
        break;
    }

    desc.setFriction(Collider.friction[entity] ?? 0.5);
    desc.setRestitution(Collider.restitution[entity] ?? 0);

    const sensor = Collider.sensor[entity] || 0;
    if (sensor) {
      desc.setSensor(true);
      desc.setDensity(0);
    } else {
      desc.setDensity(Collider.density[entity] ?? 1);
    }

    const groups = Collider.membershipGroups[entity] || 0xffff;
    const filter = Collider.filterGroups[entity] || 0xffff;
    desc.setCollisionGroups((groups & 0xffff) | ((filter & 0xffff) << 16));

    desc.setTranslation(
      Collider.posOffsetX[entity] || 0,
      Collider.posOffsetY[entity] || 0,
      Collider.posOffsetZ[entity] || 0
    );

    return desc;
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/body.ts
  git commit -m "feat(physics): create body.ts — createRapierBody + createRapierColliderDesc"
  ```

---

## Task 5: Rewrite src/plugins/physics/systems.ts

**Files:**
- Create: `src/plugins/physics/systems.ts`

- [ ] **Step 1: Write 5-system implementation**

  ```typescript
  import * as RAPIER from '@dimforge/rapier3d-simd';
  import { defineQuery, type State, type System } from '../../core';
  import { Transform } from '../transforms';
  import { Rigidbody, Collider } from './components';
  import { getOrCreateWorld, stepWorld } from './world';
  import { createRapierBody, createRapierColliderDesc } from './body';

  const bodyQuery = defineQuery([Rigidbody, Collider, Transform]);

  const stateToBodies = new WeakMap<State, Map<number, RAPIER.RigidBody>>();

  function getBodyMap(state: State): Map<number, RAPIER.RigidBody> {
    let m = stateToBodies.get(state);
    if (!m) {
      m = new Map();
      stateToBodies.set(state, m);
    }
    return m;
  }

  export const PhysicsInitSystem: System = {
    group: 'fixed',
    update: (state) => {
      const world = getOrCreateWorld();
      const bodies = getBodyMap(state);

      for (const entity of bodyQuery(state.world)) {
        if (bodies.has(entity)) continue;

        const bodyDesc = createRapierBody(entity);
        const body = world.createRigidBody(bodyDesc);
        bodies.set(entity, body);

        const colliderDesc = createRapierColliderDesc(entity);
        world.createCollider(colliderDesc, body);

        const t = body.translation();
        Rigidbody.posX[entity] = t.x;
        Rigidbody.posY[entity] = t.y;
        Rigidbody.posZ[entity] = t.z;

        const r = body.rotation();
        Rigidbody.rotX[entity] = r.x;
        Rigidbody.rotY[entity] = r.y;
        Rigidbody.rotZ[entity] = r.z;
        Rigidbody.rotW[entity] = r.w;
      }
    },
  };

  export const ApplyMovementSystem: System = {
    group: 'fixed',
    after: [PhysicsInitSystem],
    update: () => {
      // Placeholder: player plugin sets body.linvel() directly via getBodyForEntity()
    },
  };

  export const ApplyJumpSystem: System = {
    group: 'fixed',
    after: [ApplyMovementSystem],
    update: () => {
      // Placeholder: jump handled by player plugin via applyImpulse
    },
  };

  export const PhysicsStepSystem: System = {
    group: 'fixed',
    after: [ApplyJumpSystem],
    update: () => {
      stepWorld();
    },
  };

  export const PhysicsSyncSystem: System = {
    group: 'fixed',
    after: [PhysicsStepSystem],
    update: (state) => {
      const bodies = getBodyMap(state);

      for (const [entity, body] of bodies) {
        if (!state.hasComponent(entity, Rigidbody)) continue;

        const t = body.translation();
        Rigidbody.posX[entity] = t.x;
        Rigidbody.posY[entity] = t.y;
        Rigidbody.posZ[entity] = t.z;

        const r = body.rotation();
        Rigidbody.rotX[entity] = r.x;
        Rigidbody.rotY[entity] = r.y;
        Rigidbody.rotZ[entity] = r.z;
        Rigidbody.rotW[entity] = r.w;

        const v = body.linvel();
        Rigidbody.velX[entity] = v.x;
        Rigidbody.velY[entity] = v.y;
        Rigidbody.velZ[entity] = v.z;

        if (state.hasComponent(entity, Transform)) {
          Transform.posX[entity] = t.x;
          Transform.posY[entity] = t.y;
          Transform.posZ[entity] = t.z;
          Transform.rotX[entity] = r.x;
          Transform.rotY[entity] = r.y;
          Transform.rotZ[entity] = r.z;
          Transform.rotW[entity] = r.w;
          Transform.dirty[entity] = 1;
        }
      }
    },
  };

  export function getBodyForEntity(
    state: State,
    entity: number
  ): RAPIER.RigidBody | undefined {
    return getBodyMap(state).get(entity);
  }
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/systems.ts
  git commit -m "feat(physics): rewrite systems.ts — 5 systems (init, move, jump, step, sync)"
  ```

---

## Task 6: Rewrite src/plugins/physics/recipes.ts

**Files:**
- Create: `src/plugins/physics/recipes.ts`

- [ ] **Step 1: Simplify recipes**

  ```typescript
  import { BodyType, ColliderShape } from './components';

  export const rigidbodyRecipe = {
    name: 'Rigidbody',
    merge: true,
    components: ['rigidbody', 'transform'],
  };

  export const colliderRecipe = {
    name: 'Collider',
    merge: true,
    components: ['collider', 'transform'],
  };

  export const dynamicPartRecipe = {
    name: 'dynamic-part',
    components: ['rigidbody', 'collider', 'transform', 'meshRenderer'],
    defaults: {
      'rigidbody.type': BodyType.Dynamic,
      'rigidbody.mass': 1,
      'rigidbody.gravity-scale': 1,
      'rigidbody.rot-w': 1,
    },
  };
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/recipes.ts
  git commit -m "feat(physics): rewrite recipes — rigidbody + collider + dynamic-part"
  ```

---

## Task 7: Rewrite src/plugins/physics/plugin.ts

**Files:**
- Create: `src/plugins/physics/plugin.ts`

- [ ] **Step 1: Register minimal plugin**

  ```typescript
  import type { Plugin } from '../../core';
  import { Rigidbody, Collider } from './components';
  import {
    PhysicsInitSystem,
    ApplyMovementSystem,
    ApplyJumpSystem,
    PhysicsStepSystem,
    PhysicsSyncSystem,
  } from './systems';
  import { initPhysics } from './world';
  import { rigidbodyRecipe, colliderRecipe, dynamicPartRecipe } from './recipes';

  export const PhysicsPlugin: Plugin = {
    initialize: initPhysics,
    systems: [
      PhysicsInitSystem,
      ApplyMovementSystem,
      ApplyJumpSystem,
      PhysicsStepSystem,
      PhysicsSyncSystem,
    ],
    recipes: [rigidbodyRecipe, colliderRecipe, dynamicPartRecipe],
    components: {
      Rigidbody,
      Collider,
    },
    config: {
      defaults: {
        rigidbody: {
          type: 0,
          mass: 1,
          gravityScale: 1,
          rotW: 1,
        },
        collider: {
          shape: 0,
          sizeX: 1,
          sizeY: 1,
          sizeZ: 1,
          radius: 0.5,
          height: 1,
          friction: 0.5,
          restitution: 0,
          density: 1,
          sensor: 0,
          membershipGroups: 0xffff,
          filterGroups: 0xffff,
          posOffsetX: 0,
          posOffsetY: 0,
          posOffsetZ: 0,
          rotOffsetX: 0,
          rotOffsetY: 0,
          rotOffsetZ: 0,
          rotOffsetW: 1,
        },
      },
      enums: {
        rigidbody: {
          type: {
            dynamic: 0,
            fixed: 1,
          },
        },
        collider: {
          shape: {
            box: 0,
            sphere: 1,
            capsule: 2,
          },
        },
      },
    },
  };
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/plugin.ts
  git commit -m "feat(physics): rewrite plugin.ts — register minimal MVP plugin"
  ```

---

## Task 8: Rewrite src/plugins/physics/index.ts

**Files:**
- Create: `src/plugins/physics/index.ts`

- [ ] **Step 1: Update exports**

  ```typescript
  import * as RAPIER from '@dimforge/rapier3d-simd';

  export { BodyType, ColliderShape, Rigidbody, Collider } from './components';
  export { PhysicsPlugin } from './plugin';
  export { getBodyForEntity } from './systems';
  export { initPhysics, getWorld, getOrCreateWorld } from './world';
  export { createRapierBody, createRapierColliderDesc } from './body';
  export { RAPIER };
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/plugins/physics/index.ts
  git commit -m "feat(physics): rewrite index.ts — minimal exports for MVP"
  ```

---

## Task 9: Update vite.config.ts

**Files:**
- Modify: `VibeGame/vite.config.ts`

- [ ] **Step 1: Swap alias and external**

  ```typescript
  alias: {
    '@dimforge/rapier3d': '@dimforge/rapier3d-simd',
  }
  ```
  
  Replace `@dimforge/rapier3d-compat` with `@dimforge/rapier3d-simd` in `external` array.

- [ ] **Step 2: Commit**

  ```bash
  git add VibeGame/vite.config.ts
  git commit -m "build(physics): update vite config for rapier3d-simd"
  ```

---

## Task 10: Update src/vite/index.ts alias

**Files:**
- Modify: `src/vite/index.ts`

- [ ] **Step 1: Swap alias**

  ```typescript
  '@dimforge/rapier3d': '@dimforge/rapier3d-simd',
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add src/vite/index.ts
  git commit -m "build(physics): update vite alias to rapier3d-simd"
  ```

---

## Task 11: Update cross-plugin imports

**Files:**
- Modify: `src/plugins/joints/<anything>`
- Modify: `src/plugins/raycast/<anything>`
- Modify: `src/plugins/terrain/<anything>`
- Modify: `src/plugins/water/<anything>`

- [ ] **Step 1: Update all imports**

  ```bash
  rg -l "@dimforge/rapier3d-compat" src/plugins/ | xargs -I{} sed -i 's/@dimforge\/rapier3d-compat/@dimforge\/rapier3d-simd/g' {}
  ```

- [ ] **Step 2: Verify**

  ```bash
  rg "@dimforge/rapier3d-compat" src/
  ```
  
  Expected: No output.

- [ ] **Step 3: Commit**

  ```bash
  git add -A
  git commit -m "chore(physics): update all cross-plugin imports to rapier3d-simd"
  ```

---

## Task 12: Delete dead file

**Files:**
- Delete: `src/plugins/physics/utils.ts`

- [ ] **Step 1: Remove**

  ```bash
  rm src/plugins/physics/utils.ts
  git add src/plugins/physics/utils.ts
  git commit -m "refactor(physics): delete utils.ts — logic moved to body.ts + world.ts"
  ```

---

## Task 13: TypeScript check

**Files:** N/A

- [ ] **Step 1: Run typecheck**

  ```bash
  cd VibeGame && bun run check
  ```
  
  Expected: Clean.

- [ ] **Step 2: Fix errors if any**

  Fix inline. Commit if edits applied:
  
  ```bash
  git add -A && git commit -m "fix(physics): resolve typecheck errors"
  ```

---

## Task 14: Build verification

**Files:** N/A

- [ ] **Step 1: Build**

  ```bash
  cd VibeGame && bun run build
  ```
  
  Expected: Successful.

---

## Task 15: Update examples

**Files:**
- Modify: `VibeGame/examples/simple-rpg/index.html`
- Modify: `VibeGame/examples/hello-world/index.html`

- [ ] **Step 1: Replace old physics attributes**

  Convert any XML using `body=`, `collider=`, `character-controller`, `character-movement` to:
  
  ```xml
  <Rigidbody type="dynamic" mass="70" rotW="1" />
  <Collider shape="capsule" radius="0.3" height="1.6" density="1" />
  ```

- [ ] **Step 2: Commit**

  ```bash
  git add VibeGame/examples/
  git commit -m "docs(examples): update physics recipes for new MVP plugin"
  ```

---

## Self-Review Checklist

- [ ] All sections of spec covered by at least one task
- [ ] No placeholders, no TBD, no vague instructions
- [ ] Type names consistent across all files
- [ ] Zero `@dimforge/rapier3d-compat` references remain
- [ ] Examples updated

## Execution Handoff

Plan saved.

**Execution options:**

1. **Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks
2. **Inline Execution** — Execute tasks in this session with executing-plans skill

**Which approach?**
