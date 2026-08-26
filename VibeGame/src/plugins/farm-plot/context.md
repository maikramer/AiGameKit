# farm-plot

<!-- LLM:OVERVIEW -->

Tile-grid farming: one `<FarmPlot>` entity per field; tiles live in side
arrays (not entities), rendered by dirty-flushed instanced pools (soil quad
per tile + one pool per crop/stage). Pure tile state machine
(till→plant→water→grow→harvest/regrow/wither) with per-tile change listeners;
crop defs are data-driven (`crop` kind in the DataRegistry); saves as 10
bytes/tile with crop-id remap. Opt-in — **not** in `DefaultPlugins`.
<!-- /LLM:OVERVIEW -->

Tile-grid farming: one `<FarmPlot>` entity per field, instanced rendering,
pure state machine. The engine layer knows soil and crops — never inventory,
stamina or money (that belongs to the game; see examples/simple-farm).

## Layout

| File        | Responsibility |
| ----------- | -------------- |
| components  | `FarmGrid` SoA component (origin, cellSize, cols/rows, baseY, version) |
| grid        | Pure grid math: `worldToCell`, `cellToWorld`, `cellIndex`, `facingCellFrom` |
| store       | Per-tile side arrays (WeakMap<State, Map<eid, data>>) — tiles are data, not entities |
| crops       | `CropDef`, tile lifecycle (till→plant→water→grow→harvest/regrow/wither), listeners |
| render      | Instanced pools: soil quads (slot i = tile i) + one pool per (crop, stage) |
| highlight   | Translucent quad + outline on the facing tile (depthTest off) |
| systems     | Setup (intern crops, resolve baseY), draw flush, highlight follow |
| recipes     | `<FarmPlot at size cell-size base-y>` |
| serializer  | 10 bytes/tile + cropIds header with id remap on load |
| api         | `createFarmGrid`, `getFarmGrid`, `getTileState` convenience wrappers |

## Contracts

- **Setup order**: `FarmGridSetupSystem` runs `after: TerrainPadApplySystem` —
  an auto `baseY` samples the ground the pads stamped. Crop YAML must be in
  the DataRegistry (`kind: crop`) before the first `setup` pass, i.e. before
  `runtime.start()`.
- **Growth rule**: crops advance one growth day per `advanceFarmDay` call only
  when watered that day; unwatered days accumulate dry days and can wither
  (`witherAfterDays`). `advanceFarmDay` is a plain function the game calls
  from its own "sleep" flow — this plugin has no clock dependency.
- **Render is flush-only**: mutators mark tiles dirty; `FarmRenderSystem`
  pushes O(dirty) updates into instanced pools built on the rendering
  plugin's instance-slot helpers (no second instancing abstraction).
- **Saves**: `registerSaveSerializer(state, 'farm-grid', …)` from the plugin's
  `initialize`. Payload header stores the interned crop id alphabet; loads
  remap ids so adding/reordering crops never corrupts old saves.
- **Facing**: `facingCellFrom` quantizes the actor's forward to the dominant
  cardinal (ties to +Z — the engine's facing convention) and takes the
  neighbour cell of the actor's own cell; off-grid actors probe one cell
  ahead so a farmer beside the field still reaches the border tiles.

## Data notes

Tiles in side arrays, not entities: a 64×48 field is 3072 tiles ≈ 28 KB of
typed arrays versus 3072 entity rows in every transform pass and query, for
zero behavioural benefit (tiles never move or collide; rendering is
instanced either way).
