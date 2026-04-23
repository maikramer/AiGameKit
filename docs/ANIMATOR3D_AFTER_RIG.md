# Animator3D after Rigging3D (animation pass)

[Rigging3D](../Rigging3D/) produces a **rigged GLB** from the GameAssets batch when rigging is enabled (via `rigging3d` profile block or `generate_rig=true`). [Animator3D](../Animator3D/) runs **Blender (bpy)** to bake procedural animation clips into a GLB (e.g. idle, walk, run).

## Option A — Integrated in `gameassets batch` (recommended)

1. Install **Animator3D** so **`animator3d`** is on `PATH`, or set **`ANIMATOR3D_BIN`**.
2. Run **`gameassets batch --profile game.yaml --manifest manifest.csv`**. Animation is auto-detected from the manifest and profile.
3. For each row where animation should run after rig, set **`generate_animate=true`** in the manifest (or rely on auto-detection: rigged rows animate when the `animator3d` profile block exists — see [GameAssets README](../GameAssets/README.md)).
4. When Part3D is enabled, Animator3D also runs **`texture-project`** to bake the original texture onto Part3D part meshes automatically.

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

### Texture projection (Part3D parts)

After [Part3D](../Part3D/) decomposition, parts lose the original texture. `texture-project` bakes it back via Cycles:

```bash
animator3d texture-project hero_textured.glb hero_parts.glb -o hero_parts_textured.glb
```

This step is automatic when using `gameassets batch` with Part3D enabled and `animator3d` available.

## Handoff to VibeGame

1. Copy the GLB you want into `public/assets/models/` (see [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md)).
2. **Static prop:** `loadGltfToScene` or `<gltf-load url="…">`.
3. **Character with clips:** `loadGltfAnimated` + **`GltfAnimator`**, or declarative **`<player-gltf model-url="/assets/models/hero.glb">`** for idle / walk / run driven by input.

Clip names from the **`humanoid`** game-pack use the `Animator3D_*` prefix — see [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md) §3 and [Animator3D README](../Animator3D/README.md).

## See also

- [Animator3D README](../Animator3D/README.md) — `game-pack`, presets, individual commands
- [MONOREPO_GAME_PIPELINE.md](MONOREPO_GAME_PIPELINE.md) — folder layout and web contract
- [ZERO_TO_GAME_AI.md](ZERO_TO_GAME_AI.md) — end-to-end animation pipeline
