# Animator3D after Rigging3D (animation pass)

[Rigging3D](../Rigging3D/) produces a **rigged GLB** from the GameAssets batch when you use **`--with-rig`**. [Animator3D](../Animator3D/) runs **Blender (bpy)** to bake procedural animation clips into a GLB (e.g. idle, walk, run).

## Option A — Integrated in `gameassets batch` (recommended)

1. Install **Animator3D** so **`animator3d`** is on `PATH`, or set **`ANIMATOR3D_BIN`**.
2. Run **`gameassets batch --with-3d --with-rig --with-animate`**.
3. For each row where animation should run after rig, set **`generate_animate=true`** in the manifest (or rely on the default that ties animation to rigged rows when `--with-rig` is used — see [GameAssets README](../GameAssets/README.md)).

Optional **`animator3d`** block in **`game.yaml`** (e.g. `preset: humanoid`) selects the **`animator3d game-pack`** preset.

**`gameassets handoff`** can surface the animated file as the primary model when naming allows (animated GLB preferred over rig-only where applicable).

## Option B — Manual CLI

Useful for one-off files, custom `--clips` filters, or debugging:

```bash
animator3d game-pack hero_rigged.glb hero_animated.glb --preset humanoid
```

Single-clip example:

```bash
animator3d wave-idle hero_rigged.glb hero_animated.glb --frames 60
```

Inspect bones if needed:

```bash
animator3d inspect hero_rigged.glb --json-out
```

## Handoff to VibeGame

1. Copy the GLB you want into `public/assets/models/` (see [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md)).
2. **Static prop:** `loadGltfToScene` or `<gltf-load url="…">`.
3. **Character with clips:** `loadGltfAnimated` + **`GltfAnimator`**, or declarative **`<player-gltf model-url="/assets/models/hero.glb">`** for idle / walk / run driven by input.

Clip names from the **`humanoid`** game-pack use the `Animator3D_*` prefix — see [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md) §3 and [Animator3D README](../Animator3D/README.md).

## See also

- [Animator3D README](../Animator3D/README.md) — `game-pack`, presets, individual commands
- [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) — folder layout and web contract
- [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md) — end-to-end animation pipeline
