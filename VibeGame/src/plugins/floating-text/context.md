# Floating Text Plugin

<!-- LLM:OVERVIEW -->
World-space floating text — pickup popups ("+1 Pedra"), damage numbers, quest
pings. Real 3D text via `troika-three-text` (SDF glyphs), billboarded to the
active camera, drifting upward and fading out before self-destroying. Spawn
imperatively with `spawnFloatingText(state, '+1 Pedra!', { x, y, z, color })`;
strings live in a sidecar map (SOA fields are numeric).
<!-- /LLM:OVERVIEW -->

## Layout

```
floating-text/
├── components.ts   # FloatingText SOA (elapsed/duration/riseSpeed/size/color)
├── utils.ts        # spawnFloatingText + string sidecar
├── systems.ts      # FloatingTextUpdateSystem (group: draw)
├── plugin.ts       # FloatingTextPlugin
└── index.ts
```

<!-- LLM:REFERENCE -->
### Component

#### floating-text
- elapsed: f32 — advanced by the system
- duration: f32 (1.4) — lifetime in seconds; entity destroyed at the end
- riseSpeed: f32 (0.9) — upward drift m/s
- size: f32 (0.35) — font size in world meters
- colorR/G/B: f32 (1)

### System

#### FloatingTextUpdateSystem
- Group: `draw`; no-op when `state.headless`
- Lazily creates a troika `Text` per entity (centered anchors, 6% black
  outline, `renderOrder` 999 + `depthOffset` -4 so props don't slice glyphs)
- Billboards to the first camera in `threeCameras`, rises with elapsed time,
  fades the second half of the lifetime, destroys the entity at `duration`
- Disposes `Text` objects for destroyed entities and on plugin dispose

### API

`spawnFloatingText(state, text, { x, y, z, color?, size?, duration?, riseSpeed? }) → eid`

## Key Rules
- Strings cannot live in SOA components — sidecar map keyed by entity, cleaned
  in dispose.
- troika `Text.sync()` must run after changing text properties (opacity each
  frame); position/quaternion are plain Object3D state and need no sync.
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples
```ts
import { spawnFloatingText } from 'vibegame';

spawnFloatingText(state, '+1 Pedra!', {
  x: rockX, y: rockY + 1, z: rockZ,
  color: 0xffd27a, size: 0.4,
});
```
<!-- /LLM:EXAMPLES -->
