# Destructible Plugin

<!-- LLM:OVERVIEW -->
Player-breakable props. Add `destructible="…"` to an entity: swinging the
primary attack (left click / mapped key) within range commits a hit that lands
near the end of the attack clip (synced to the swing animation); on the final
hit the prop bursts into particles, optionally shows a floating popup, fires
`onDestructibleDestroyed` (loot/inventory hook) and destroys the entity.
Builds on the particles and floating-text plugins.
<!-- /LLM:OVERVIEW -->

## Layout

```
destructible/
├── components.ts   # Destructible SOA
├── utils.ts        # popup-text sidecar + onDestructibleDestroyed hook
├── systems.ts      # DestructibleSystem (group: simulation)
├── plugin.ts       # DestructiblePlugin (defaults, preset enum, adapters)
└── index.ts
```

<!-- LLM:REFERENCE -->
### Component

#### destructible
- hits: ui8 (1) — swings needed to break
- hitsTaken: ui8
- range: f32 (3.5) — attack reach in meters
- impactFraction: f32 (0.75) — fraction of the attack clip when the blow lands
- pendingImpact: f32 — internal countdown; 0 = idle
- preset: ui8 (explosion) — particle preset for the break burst
- burstCount: f32 (60)
- faceOnHit: ui8 (1) — snap player yaw toward the prop on swing
- sparkOnHit: ui8 (1) — sparks feedback on non-final hits
- popupColorR/G/B: f32 (1) — set via `popup-color: #d4c9a8`
- popupSize: f32 (0.4)

Adapters: `popup-text` (string sidecar — popup only shows when set),
`popup-color` (hex).

### System

#### DestructibleSystem
- Group: `simulation`
- Swing input: `InputState.primaryAction` on the `PlayerController` entity
  (buffered; left click or any key bound via `addInputMapping`)
- One swing per 0.4s, committed to the nearest destructible within its range
- Impact delay derives from the player's attack clip duration
  (`PlayerGltfConfig.animatorRegistryIndex` → animator) × `impactFraction`,
  falling back to 0.5s without an animator

### API

`onDestructibleDestroyed(state, (eid, x, y, z) => void) → unsubscribe` —
game hook for loot/inventory/SFX.
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples
```xml
<GameObject
  place="at: 8 -6; align-to-terrain: 0"
  destructible="popup-text: +1 Pedra!; popup-color: #d4c9a8"
  rigidbody="type: fixed; mass: 0"
  collider="shape: trimesh; mesh-url: /assets/meshes/rock_mossy_collision.glb; mesh-anchor: base"
>
  <GLTFLoader url="/assets/meshes/rock_mossy_lod0.glb" scale="1.2 1.2 1.2" />
</GameObject>
```

```ts
import { onDestructibleDestroyed } from 'vibegame';

onDestructibleDestroyed(state, (eid, x, y, z) => {
  addStone(1, x, y, z); // inventory, SFX, quest counters…
});
```
<!-- /LLM:EXAMPLES -->
