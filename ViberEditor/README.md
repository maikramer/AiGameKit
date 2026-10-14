# ViberEditor

Godot-style editor for [Viber](../Viber) worlds — the manual/debugging counterpart of the
agent-first engine. Every edit goes through the engine's own editing API (`viber::scene`), the
same one agents use via `viber scene …` and the `viber.scene.*` bridge: an edit made here and
the same edit made by an agent produce the same XML.

By default it opens the **static** world (`world.static.xml`, produced by `viber bake` — the
source of truth); a procedural `world.xml` still opens, read as-is.

The editor links the engine as a library and loads the world **in-process** in
`viber::app::RunMode::Edit`: the scene renders live in the viewport at full frame rate, the
simulation is frozen (virtual time paused, Luau `on_update` off, no player input) and the editor
camera replaces the game camera. **Play (F5)** saves and runs the world as a separate
`viber run --bridge` process — the game never shares a window or a UI with the editor.

## Layout

```
┌ Toolbar: ▣ · Save · Play (F5) · Play here (F9) · Pause (F6) · Stop (F8) · lang · ● ─┐
├ Objects(+)┬ Viewport (editor camera) ─────────────────┬ Inspector ───┤
│ world     │ ▤ tool strip (Q/W/E/R/B · T · F)          │ the selected  │
│ tree,     │ 3D | UI | Script selector (top-center)     │ node's        │
│ Includes  ├ Console | Logs | World Overview | Files ───┤ attributes    │
└───────────┴────────────────────────────────────────────┴───────────────┘
```

The UI is **bilingual: Portuguese by default, English via the toolbar's globe button or
`--lang en`** — `src/ui/strings.rs` owns every user-visible name. Names are friendly
("Objetos", "Posição", "Terreno"); the raw XML keys stay visible in small gray in the
Inspector and unchanged in the files/CLI, so agents and the editor keep editing the same
document.

- **Objects** — the world's node tree (rows show each node's own name; the icon carries the
  type). `<Include>` nodes expand into the included file (sub-scenes, shown in blue) and show
  a humanized name without the extension ("village.xml" → "Village" — the file icon already
  says it's a file); an include with a `translation`/`euler`/`scale` can be selected and
  dragged like a group, moving everything the file declares. The header has a **search
  box** (Enter filters by name/tag, Esc clears). Each file is kept as its own
  document so saving writes back only the files that changed (`src/scene/doc.rs`).
- **Viewport** — Viber's editor camera renders into an image shown by a `ViewportNode`.
  Middle drag orbits, Shift + middle drag pans, wheel zooms; right drag looks around and WASD/QE
  fly while it is held (wheel changes fly speed, Shift boosts).
- **Tool strip** — the floating vertical strip over the viewport's top-left corner is the
  gizmo, clickable: select / move / rotate / scale / terrain brush, local/global axes and
  frame-selection, mirroring the Q/W/E/R/B · T · F shortcuts. The active tool stays
  highlighted; the axes toggle flips its cube (local) / globe (global) icon.
- **Pause (F6)** — freezes the *running game's* simulation through the bridge
  (`viber.debug.step(0)` / `viber.debug.play()`), editor-style: the button latches with a play
  icon while paused and the status line says so. Play always starts unpaused.
- **UI separation** — the editor panels use their own UI camera; the game's UI (HUD, menus,
  loading screen) targets an inactive default UI camera, so it never draws over the editor.
- **Inspector (v2, Godot-style)** — typed property SECTIONS with collapsible headers on
  top of the raw attrs: **Transform** (translation m / euler° / scale × as scrubbing number
  inputs with X/Y/Z sigils — drag to scrub, Shift for fine steps; the commit is the same
  canonical `pose_op` the gizmo writes, and a scrub previews live on the entities),
  **Physics** (collider shape dropdown with per-shape fields — `size`/`radius`/`half-height`/
  `mesh-url` — plus rigidbody type and gravity-scale, written into the `collider`/
  `rigidbody` component-strings preserving unknown keys), **Light** (color picker,
  intensity lm / illuminance lux, shadows), **Shape + Material** (primitive dimensions,
  base-color/emissive pickers, metallic/roughness/opacity), **GLTF/LODs**. The node's
  `name` and direct text (`<UiText>`) are editable fields; **Ctrl+C/Ctrl+V** copy-paste
  nodes. Whatever no section covers stays as raw text attrs — friendly name plus the raw
  XML key in small gray: Enter commits (an empty value removes the attribute); the last
  row takes `key=value` to add one. Every typed field carries a hover tooltip with its unit.
- **Selection & gizmo** — click in the viewport picks the most specific declared node under
  the cursor and reveals it in the Objects dock (yellow bounds). Q select · W move · E rotate ·
  R scale · T local/global · Ctrl snaps (1 m / 15° / 0.1) · Esc cancels a drag · F frames.
  Releasing a drag commits one canonical pose edit (`translation`/`euler`/`scale`).
- **History** — Ctrl+Z / Ctrl+Shift+Z (Ctrl+Y) undo/redo, Ctrl+D duplicate, Delete remove,
  Ctrl+S save. Edits sync to the live scene immediately; kinds that can't spawn incrementally
  (terrain features, sky, UI, spawners) are flagged in the toolbar as needing a reload —
  **Ctrl+R** saves and relaunches the editor on the same world, keeping the selection.
- **Console / Logs** — the Luau REPL and log ring buffer of the running game (Play). The Logs
  header has the **remote tree** button: dumps the running game's entity tree (`viber.tree`)
  into the panel — the lean Debugger.
- **Profiler** — the Debugger's live systems panel: polls `viber.debug.prof()` on the
  running game every ~1.5 s (fps, frame ms avg/p95, top systems by average, hot rows in red).
- **World Overview** — baked world summary + hillshade minimap (`--bake <dir>` or
  `<world dir>/bake`). The minimap is **clickable**: the editor camera jumps to the clicked
  world XZ.
- **Files** — the world's folder as a tree (the same scan as `viber scene files`): filter by
  kind (All/Scenes/GLB/Scripts), select a file for its action — a `.glb` inserts into the
  scene at the terrain under the cursor, a scene XML opens in a **new editor window**, any
  file opens externally (`xdg-open`).
- **Add node** — the `+` in the Objects header opens the template catalog
  (`viber::scene::catalog`): the same snippets `viber scene insert` applies, inserted under
  the selection (or the world root).
- **Drag-and-drop** — drop a `.glb` from the file manager over the window: it lands on the
  terrain under the cursor as one undoable insert (`catalog::glb_drop`).
- **3D | UI | Script** — the top-center selector. **UI** shows the game's UI alone,
  letterboxed 16:9: the selected `<UiRoot>` subtree gets its computed rect on the canvas with
  MOVE/RESIZE handles (drag the body / a corner — one canonical `set` op per release, only for
  nodes with explicit `left/top/width/height`), a **Normal/Hover/Pressed state preview** (the
  engine's real `:hover`/`:active` cascade via `UiPreviewState`) and a **menu_data line** for
  `<UiList>` selections (the resolved rows). **Script** lists the world's `.lua` files with a
  preview and "open externally".
- **Terrain brushes & settings panel** — B opens the brush AND a floating settings panel
  (top-right): radius/strength sliders and the material-layer dropdown, synced with the
  keys. Shortcuts: `1` raise · `2` lower · `3` smooth · `4` flatten ·
  `5` crater · `7` noise · **`6` paints material** (`,` / `.` cycle the pool layer — grass, dirt_trail,
  mountain_stone…; the `terrain_paint` op, persisted in the `.paint` sidecar next to the
  `.ahgt`) · `[` / `]` radius · `-` / `=` strength. A stroke is ONE undo step.
- **Settle on terrain** — Inspector button on groups/props (`viber tool settle`): after
  terrain brushes, puts the node (a group: its children) back on the current ground. Static
  instances never re-seat on their own.
- **Feature handles** — selecting a `<Lake>`/`<TerrainPad>`/`<River>`/`<Road>` draws its
  outline on the terrain; drag a handle to move that point (one canonical `set` per release).
- **Play from here (F9)** — runs the game and teleports the hero to where the editor camera
  looks (`viber.debug.tp` once the bridge connects).

## Visual identity

- **Icon set** — every button, dock header, tab and tree row carries an icon from an
  embedded **Font Awesome 6 Free (solid)** font (`src/assets/fonts/fa-solid-900.ttf`,
  CC BY 4.0 / OFL — see `fa-solid-900.LICENSE.txt`). Glyphs are plain `Text` spans with
  their own font (`src/ui/icons.rs`); codepoints in the `fa` module are validated against
  the font's cmap, so new UI can reach for them without re-checking. Tree rows get a
  per-tag icon with a category color (lights amber, water blue, terrain green…).
- **Custom art (text2d)** — the toolbar logo, the Inspector's empty-state cursor/cube
  illustration and the World Overview's no-bake map were generated with
  `text2d generate … --category icon --transparent` and embedded at
  `src/assets/icons/`.
- **Tooltips** — hover any icon button for a short delay and a Godot-style tooltip shows
  the action and its shortcut (`src/ui/tooltip.rs`; add a `Tooltip("…")` component).
- **Status dot** — the dot next to the status line is colored by game state: gray stopped,
  amber starting, green running, red failed.

## Usage

```bash
cd ViberEditor
cargo run                                 # Viber's simple-rpg demo
cargo run -- path/to/world.xml            # a specific world
cargo run -- --screenshot out.png --frames 500   # capture the editor window and exit (QA)
cargo run -- --select name=chapel --screenshot out.png   # select + frame a node first
cargo run -- --tab files --screenshot out.png          # open a bottom-dock tab at startup
cargo run -- --mode ui --select id=vitals --screenshot out.png   # UI canvas + handles on a HUD node
cargo run -- --mode script --screenshot out.png        # 3d | ui | script at startup
```

Options: `--port <PORT>` for the Play bridge, `--viber-bin <PATH>` for the engine used by Play
(default: `viber` on `PATH`, else `Viber/target/{release,debug}/viber`), `--lang pt|en` for the
UI language (default: pt; also switchable at runtime from the toolbar).

Both crates share the engine source; there is no Bevy checkout prerequisite (Bevy 0.20 from
crates.io, the same as Viber). `[patch.crates-io]` mirrors Viber's `rerecast` patch.

## Development

```bash
make test-vibereditor   # from the AiGameKit root
cargo clippy -- -D warnings
cargo test
```

Engine edits in `../Viber/src` are picked up on the next editor build. Running the editor counts
as a Viber instance: use `Viber/scripts/instance-lock.sh exec -- …` when other agents are active.

## Roadmap

Done (Fases 2–5 + polish):

1. ✅ Terrain editor: raise/lower/smooth/flatten/crater brushes AND material paint
   (`terrain_paint`), persisted (`.ahgt` + `.paint`), live re-mesh/re-paint, one undo per
   stroke — the same ops agents use.
2. ✅ Generators as tools (`<Scatter>` definitions run by the tool/editor/CLI) and
   pre-generated vegetation (`.vinst`).
3. ✅ Files dock (filter/actions), Add Node catalog, drag-and-drop GLB.
4. ✅ 3D | UI | Script selector (game-UI canvas letterboxed; script browser with preview).
5. ✅ "Play from here" (F9), clickable World-Overview minimap, remote entity tree
   (`viber.tree` dump in Logs) and the live **Profiler** dock (`viber.debug.prof`).
6. ✅ UI-canvas editing: move/resize handles, state preview (hover/pressed) and the
   `menu_data` line.

7. ✅ Review pass: rect-scoped live paint sync, frame-correct GLB drop (world → parent),
   `settle` and `canonicalize` tools, Ctrl+R reload, dropped/added nodes get selected.

Next: feature point handles for `<RoadNetwork>`, a Regenerate button for
`<Vegetation instances>` in the Inspector (agents already regenerate via
`viber tool scatter <sel>` — writes the `.vinst` atomically), a text editor in
the Script mode (today read-only preview + open externally), and rendering the
game UI to the `GameUiCamera` in UI mode (the letterbox stays empty —
`KeepCameraActive` is set but the UI pass doesn't target that camera yet).
