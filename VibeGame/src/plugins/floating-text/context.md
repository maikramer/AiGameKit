# Floating Text Plugin

<!-- LLM:OVERVIEW -->
Floating text with **two rendering modes** sharing one SOA component:

- **World** (`space: 'world'`, default): troika-three-text SDF glyphs in the 3D
  scene, billboarded to the active camera, drifting upward and fading out.
- **Screen** (`space: 'screen'`): DOM `<span class="vibe-float-screen">`
  recycled through a fixed-size pool (default 32) and mounted inside the
  `HudScreenLayer`. Uses CSS pixel coordinates, supports crit styling.

Spawn imperatively with `spawnFloatingText(state, text, opts)` or the screen
convenience wrapper `spawnFloatingTextScreen(state, text, opts)`. Strings live
in a sidecar map (SOA fields are numeric).
<!-- /LLM:OVERVIEW -->

## Layout

```
floating-text/
├── components.ts   # FloatingText SOA (elapsed/duration/riseSpeed/size/color + space/screenX/Y/fontSizePx/driftX/crit)
├── utils.ts        # spawnFloatingText + spawnFloatingTextScreen + string sidecar
├── screen-pool.ts  # ScreenFloatPool — DOM span recycler inside HudScreenLayer
├── systems.ts      # FloatingTextUpdateSystem (world, group: draw) + FloatingTextScreenUpdateSystem (group: late)
├── plugin.ts       # FloatingTextPlugin — registers both systems
└── index.ts
```

<!-- LLM:REFERENCE -->
### Component

#### floating-text
- elapsed: f32 — advanced by the system
- duration: f32 (1.4) — lifetime in seconds; entity destroyed at the end
- riseSpeed: f32 — world mode: m/s (0.9). Screen mode: px/s (50).
- size: f32 (0.35) — font size in world meters (world mode)
- colorR/G/B: f32 (1)
- space: ui8 (0) — 0 = world (troika 3D), 1 = screen (DOM pool)
- screenX/Y: f32 (0) — initial screen position in CSS px (screen mode)
- fontSizePx: f32 (0) — font size in CSS px (screen mode; 0 → default 20 or 26 if crit)
- driftX: f32 (0) — signed horizontal drift in px (screen mode; random [-17,17] when omitted)
- crit: ui8 (0) — crit flag (screen mode): bigger font + red-orange tint

### Systems

#### FloatingTextUpdateSystem (world)
- Group: `draw`; runs after `CameraSyncSystem`; no-op when `state.headless`
- Skips entities with `space === 1`
- Lazily creates a troika `Text` per entity (6% black outline, renderOrder 999)
- Billboards to the active camera, rises with elapsed time, fades the second
  half of the lifetime, destroys the entity at `duration`

#### FloatingTextScreenUpdateSystem (screen)
- Group: `late`; no-op when `state.headless` or no DOM
- Skips entities with `space === 0`
- Lazy-creates the `ScreenFloatPool` on first screen entity
- Animates DOM spans: rise + horizontal drift + scale-pop + fade
- Releases spans back to the pool when entities are destroyed

### API

```ts
spawnFloatingText(state, text, {
  x, y, z?, color?, size?, duration?, riseSpeed?,
  space?: 'world' | 'screen',           // default 'world'
  fontSizePx?, driftX?, crit?,
}) → eid

spawnFloatingTextScreen(state, text, {
  x, y, color?, duration?, riseSpeed?, fontSizePx?, driftX?, crit?,
}) → eid
```

`color` accepts `0xRRGGBB` (number) or `'#rrggbb'` / `'#rgb'` (string).

## Key Rules
- Strings cannot live in SOA components — sidecar map keyed by entity.
- The screen pool is a singleton per `State` (WeakMap); size is fixed at first
  access (default 32). When exhausted, the oldest live entry is evicted and its
  entity destroyed.
- World mode requires `Transform` + `WorldTransform`; screen mode does not add
  them (no 3D position needed).
- troika `Text.sync()` must run after changing text properties; position/quaternion
  are plain Object3D state and need no sync.
<!-- /LLM:REFERENCE -->

<!-- LLM:EXAMPLES -->
## Examples
```ts
import { spawnFloatingText, spawnFloatingTextScreen } from 'vibegame';

// World-space (3D troika text)
spawnFloatingText(state, '+1 Pedra!', {
  x: rockX, y: rockY + 1, z: rockZ,
  color: 0xffd27a, size: 0.4,
});

// Screen-space (DOM span in HudScreenLayer)
spawnFloatingTextScreen(state, '-15', {
  x: 320, y: 240,
  color: '#ff4444', crit: true,
});

// Or via the unified API
spawnFloatingText(state, 'CRIT 42!', {
  space: 'screen', x: 320, y: 240, crit: true,
});
```
<!-- /LLM:EXAMPLES -->
