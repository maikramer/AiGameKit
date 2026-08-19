# Transforms Plugin

<!-- LLM:OVERVIEW -->
3D transforms with position, rotation, scale, and parent-child hierarchies.
<!-- /LLM:OVERVIEW -->

## Layout

```
transforms/
├── context.md  # This file
├── index.ts  # Public exports
├── plugin.ts  # Plugin definition
├── components.ts  # Transform and WorldTransform components
├── systems.ts  # TransformHierarchySystem
└── utils.ts  # Transform conversion utilities
```

## Scope

- **In-scope**: 3D transforms, hierarchies, world/local space conversion, euler/quaternion sync
- **Out-of-scope**: Physics transforms, animations, tweening

## Entry Points

- **plugin.ts**: TransformsPlugin definition
- **systems.ts**: Transform hierarchy updates in simulation phase
- **components.ts**: Core transform components (Transform, WorldTransform)

## Dependencies

- **Internal**: Core ECS, Parent component from recipes
- **External**: Three.js math (Matrix4, Vector3, Quaternion, Euler)

<!-- LLM:REFERENCE -->
### Components

#### Transform
- posX, posY, posZ: f32 (0)
- rotX, rotY, rotZ, rotW: f32 (rotW=1) - Quaternion
- eulerX, eulerY, eulerZ: f32 (0) - Degrees
- scaleX, scaleY, scaleZ: f32 (1)

#### WorldTransform
- Same properties as Transform
- Auto-computed from hierarchy (read-only)

### Systems

#### TransformHierarchySystem
- Group: simulation (last)
- Syncs euler/quaternion and computes world transforms

#### Facing helpers (`utils.ts`)
- `planarYawRadians(dx, dz)` — `atan2(dx, dz)` (+Z forward at 0)
- `setTransformYawRadians(transform, eid, yawRad)` — writes **degrees** into `eulerY` + syncs quat + dirty
- `setTransformFacingXZ(transform, eid, dx, dz, yawOffsetRad?)` — face a planar direction

Never write `Math.atan2(...)` directly into `Transform.eulerY` (radians vs degrees); hierarchy sync will treat radians as ~±3° and agents moonwalk / strafe-face-wrong.
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples

### Basic Usage

#### XML Position and Rotation
```xml
<!-- Position only -->
<GameObject transform="pos: 0 5 -3"></GameObject>

<!-- Euler rotation (degrees) -->
<GameObject transform="euler: 0 45 0"></GameObject>

<!-- Scale (single value applies to all axes) -->
<GameObject transform="scale: 2"></GameObject>

<!-- Combined transform -->
<GameObject transform="pos: 0 5 0; euler: 0 45 0; scale: 1.5"></GameObject>
```

#### JavaScript API
```typescript
import * as GAME from 'vibegame';

// In a system
const MySystem = {
  update: (state) => {
    const entity = state.createEntity();
    
    // Add transform component with initial values
    state.addComponent(entity, GAME.Transform, {
      posX: 10, posY: 5, posZ: -3,
      eulerX: 0, eulerY: 45, eulerZ: 0,
      scaleX: 2, scaleY: 2, scaleZ: 2
    });
    
    // Transform system automatically syncs euler to quaternion
  }
};
```

### Transform Hierarchy

#### Parent-Child Relationships
```xml
<!-- Parent at origin -->
<GameObject transform="pos: 0 0 0">
  <!-- Children positioned relative to parent -->
  <GameObject transform="pos: 2 0 0"></GameObject>
  <GameObject transform="pos: -2 0 0"></GameObject>
</GameObject>

<!-- Rotating parent affects all children -->
<GameObject transform="euler: 0 45 0">
  <GameObject id="arm" transform="pos: 0 2 0">
    <GameObject id="hand" transform="pos: 0 2 0"></GameObject>
  </GameObject>
</GameObject>
```

#### Accessing World Transform
```typescript
import * as GAME from 'vibegame';

const transformQuery = GAME.defineQuery([GAME.Transform, GAME.WorldTransform]);
const WorldTransformSystem = {
  update: (state) => {
    // Query entities with both transforms
    const entities = transformQuery(state.world);
    
    for (const entity of entities) {
      // Local position
      const localX = GAME.Transform.posX[entity];
      
      // World position (after parent transforms)
      const worldX = GAME.WorldTransform.posX[entity];
      
      console.log(`Local: ${localX}, World: ${worldX}`);
    }
  }
};
```

### Common Patterns

#### Setting Transform Values
```typescript
import * as GAME from 'vibegame';

// Direct property access (bitECS style)
GAME.Transform.posX[entity] = 10;
GAME.Transform.posY[entity] = 5;
GAME.Transform.posZ[entity] = -3;

// Using euler angles for rotation
GAME.Transform.eulerX[entity] = 0;
GAME.Transform.eulerY[entity] = 45;
GAME.Transform.eulerZ[entity] = 0;
// Quaternion will be auto-synced by TransformHierarchySystem

// Uniform scale
GAME.Transform.scaleX[entity] = 2;
GAME.Transform.scaleY[entity] = 2;
GAME.Transform.scaleZ[entity] = 2;
```

#### Transform Interpolation
```typescript
import * as GAME from 'vibegame';

// Interpolate between two positions
const t = 0.5; // 50% between start and end
GAME.Transform.posX[entity] = startX + (endX - startX) * t;
GAME.Transform.posY[entity] = startY + (endY - startY) * t;
GAME.Transform.posZ[entity] = startZ + (endZ - startZ) * t;
```
<!-- /LLM:EXAMPLES -->

## Euler vs quaternião — quem escreveu por último ganha

`Transform` guarda as duas representações e há escritores dos dois lados: o XML
e o gameplay escrevem `eulerY`, a física e a animação escrevem `rotX..rotW`.
Comparar uma com a outra **não** diz quem escreveu (uma divergência é igual nos
dois sentidos), por isso `resolveRotationSource` guarda o par resolvido por
entidade (`recordRotationShadow`) e o lado que mudou desde então é o autor.

- Sem histórico (ou os dois lados mudaram no mesmo frame — que é o aspeto de um
  id de entidade reciclado): quaternião identidade + Euler não-zero = alguém a
  autorar ângulos; caso contrário ganha o quaternião.
- O shadow é **por `State`**: os arrays de componentes são globais, e um `State`
  novo reutiliza o id 1 com outra pose — sem o carimbo do dono, a segunda cena
  (ou o teste seguinte no ficheiro) perdia as rotações autoradas.

Regressão que isto fecha: `<Group rotation="0 90 0">` não rodava e os filhos não
orbitavam (22 testes de hierarquia).
