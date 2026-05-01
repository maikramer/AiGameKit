# README Documentation Rewrite — Design Spec

**Date:** 2026-05-01
**Status:** Draft
**Scope:** All 15 package READMEs in the GameDev monorepo

---

## 1. Objective

Rewrite all 15 package READMEs to be **self-contained, comprehensive references** with full CLI command tables, all flags, complete configuration reference, examples, and pipeline integration notes. The largest effort is GameAssets, which needs exhaustive documentation of manifest, game.yaml, categories, quality profiles, and all sub-commands.

## 2. Language

English for all READMEs. (Portuguese translations are out of scope for this pass.)

## 3. Approach

**Uniform template** across all packages. Each README follows the same section structure adapted per package needs.

### Universal Template

```markdown
# Package Name — Tagline
> Brief one-sentence description

## Overview
Brief description of what the tool does, its AI model / backend, and where it fits in the pipeline.

## Installation
Step-by-step install commands. Dependencies. Python version requirements.

## Commands
For each command: description, usage synopsis, full flag table, examples.

## Configuration
Tool-specific config: quality presets, categories, model options, etc.

## Environment Variables
Table of supported env vars with description.

## Output Layout
Directory structure of generated files.

## Pipeline Integration
How this tool connects with others in the monorepo.

## Development
Install dev deps, run tests, lint commands.

## License
License info.
```

## 4. Per-Package Plans

### 4.1 Shared (`Shared/README.md`) — ~150-180 lines

- **Overview:** Utility library used by all Python packages
- **Commands:** N/A (library, not CLI)
- **Modules table:** logging, gpu, subprocess_utils, installer, cli_rich, quality, multi_gpu, profiler, progress, path_utils, hf, seed_utils, quantization, bpy_mesh, vram_monitor, sdnq, skill_install, perfstore
- **QualityEngine section:** 5 tiers, 14 asset categories, 11 audio kinds, soft resolution via ParameterSource
- **MultiGPUPlanner:** Auto-detect GPUs, split weights via accelerate
- **ProfilerSession:** CPU/RAM/GPU profiling, SQLite perf DB, JSONL spans

### 4.2 Text2D (`Text2D/README.md`) — ~180-220 lines

- **Commands:** generate (12 flags), generate-batch (JSONL), info, doctor, models, skill install
- **Quality presets table:** 5 tiers with default resolution/steps/guidance
- **Output:** PNG images in flat or split layout
- **Pipeline:** Feed images to Text3D (image→3D) or GameAssets batch

### 4.3 Texture2D (`Texture2D/README.md`) — ~180-220 lines

- **Commands:** generate, presets, batch, info, skill install
- **Materialize integration:** `--materialize` flag auto-generates PBR maps
- **Quality presets table:** 5 tiers
- **Pipeline:** Seamless textures for game assets; PBR pipeline via Materialize

### 4.4 Skymap2D (`Skymap2D/README.md`) — ~180-220 lines

- **Commands:** generate (--format png/exr, --exr-scale), presets, batch, info, skill install
- **Auto-correction notes:** Auto-resize from 1024×768 → 2048×1024, vertical shift 50% for pole correction
- **PMREM integration:** Three.js `PMREMGenerator` usage with equirect convention
- **Quality presets table:** 5 tiers

### 4.5 Text3D (`Text3D/README.md`) — ~280-350 lines

- **Commands:** generate (25+ flags, text→3D + image→3D modes), generate-batch, lod, remesh, remesh-textured, collision, align-plus-z, convert, gpu-processes, doctor, info, models, skill install
- **Mesh topology section:** prepare_mesh_topology defaults (merge vertices, non-manifold repair, weld, Taubin smoothing, isotropic remeshing)
- **LOD section:** lod1_ratio/lod2_ratio, min_faces, meshfix, painted-mesh
- **Quality presets table:** 5 tiers with preset mapping (fast/balanced/hq)
- **Pipeline:** Central mesh operations hub; GameAssets delegates all mesh ops here

### 4.6 Paint3D (`Paint3D/README.md`) — ~220-280 lines

- **Commands:** texture (18 flags), texture-batch, quick (solid/perlin styles), vertex-pbr (7 presets), doctor, info
- **Quick paint section:** solid color, Perlin noise (tint, frequency, octaves, contrast)
- **Vertex PBR presets:** default, skin, floor, metal, fabric, wood, stone
- **Quality presets table:** 5 tiers with view/texture-size mapping
- **Pipeline:** After Text3D shape generation; before Rigging3D

### 4.7 Part3D (`Part3D/README.md`) — ~150-200 lines

- **Commands:** decompose (18 flags including quantization options)
- **Quantization section:** auto, none, int8, int4, torchao-int8, torchao-int4, sdnq-* presets
- **--no-quantize-dit flag:** Separate DiT quantization control
- **Quality presets table:** 5 tiers
- **Pipeline:** After Text3D/Paint3D; before Rigging3D

### 4.8 Rigging3D (`Rigging3D/README.md`) — ~180-220 lines

- **Commands:** pipeline (main), skeleton, skin, merge
- **Global flags:** --root, --python, --profiler, --gpu-ids
- **Python requirement:** Python 3.11 + bpy 5.0.1 + open3d (incompatible with bpy 5.1)
- **Pipeline:** After Part3D; feeds Animator3D
- **Draco compression:** --draco flag on merge

### 4.9 Animator3D (`Animator3D/README.md`) — ~350-450 lines

- **Commands:** game-pack (main), 15 animation commands, texture-project, screenshot, inspect-rig, inspect, export, list-clips, check
- **Animation command groups:**
  - Humanoid: walk, run, jump, fall, attack, wave-idle, breathe-idle
  - Creature/Flying: hover, soar, dive, fire, land, roar
- **Common flags:** --draco, --append, --clip-name
- **game-pack presets:** humanoid, creature, flying
- **Python requirement:** Python 3.13 + bpy 5.1.0
- **Pipeline:** Final stage after Rigging3D; produces animated GLB for VibeGame

### 4.10 GameDevLab (`GameDevLab/README.md`) — ~280-350 lines

- **6 command groups:** check, debug, bench, perf, profile, mesh
- **check glb:** YAML/JSON rule validation (CI-ready, exit 0/1)
- **debug:** screenshot, bundle, inspect, inspect-rig, compare (SSIM, MAE, RMSE)
- **bench:** part3d, paint-vram, pre-quantize, sdnq-sweep, pipeline-opt, batch
- **perf:** list, show, summary, vram, recommend, clean
- **profile:** cProfile wrapper
- **mesh:** inspect, qa, render-views, diff
- **Mesh comparison workflow section:** Step-by-step with --fail-below-ssim

### 4.11 Materialize (`Materialize/README.md`) — ~150-180 lines

- **Commands:** default (input→6 PBR maps), skill install
- **PBR maps generated:** height, normal, metallic, smoothness, edge, ao
- **Presets:** default, skin, floor, metal, fabric, wood, stone
- **Format options:** png, jpg, tga, exr
- **Pipeline:** After Texture2D or Paint3D; integrated via GameAssets

### 4.12 Terrain3D (`Terrain3D/README.md`) — ~120-160 lines

- **Commands:** generate (full flag table)
- **Quality presets table:** 5 tiers
- **Output:** heightmap.png + terrain.json (metadata)
- **Pipeline:** Standalone; integrated in GameAssets dream

### 4.13 GameAssets (`GameAssets/README.md`) — ~600-800 lines

**This is the largest README.** Sections:

1. **Overview & Pipeline Flow:** ASCII diagram of the full pipeline
2. **Installation**
3. **Commands:** Full tables for all 11 commands:
   - `init` — create game.yaml + manifest.yaml
   - `info` — show resolved binaries + VRAM
   - `prompts` — preview prompts (no GPU)
   - `batch` — full pipeline execution (20+ flags)
   - `resume` — smart state detection (_ROW_NEED_* states)
   - `dream` — idea-to-game (LLM providers, Vite scaffold)
   - `handoff` — mesh priority, audio format conversion
   - `validate` — asset validation thresholds
   - `mesh reorigin-feet` — GLB repositioning
   - `debug` — screenshot/inspect/compare/bundle
   - `skill install` — Cursor skill
4. **Manifest Reference:**
   - Full field table (id, idea, kind, pipeline[], image_source, category, lod_levels, generation, audio{}, part3d{})
   - Pipeline keywords: 3d, audio, rig, animate, parts, lod, collision
   - Complete YAML example
5. **game.yaml Reference:**
   - Root fields table (title, genre, tone, style_preset, negative_keywords, output_dir, path_layout, etc.)
   - Per-tool block tables: text2d{}, texture2d{}, skymap2d{}, text3d{}, paint3d{}, text2sound{}, rigging3d{}, animator3d{}, part3d{}, lod{}, collision{}
   - Complete YAML example
6. **Asset Categories:** 15 categories table (name, target_faces, default_kind, hints)
7. **Generation Quality Profiles:** 5 profiles comparison table
8. **Style Presets:** 4 built-in (lowpoly, pixel_art, painterly, realistic_stylized) + custom
9. **Output Layout:** Directory tree example
10. **Environment Variables:** 18 variables table
11. **Pipeline Integration:** How GameAssets orchestrates all sub-tools
12. **Development & License**

### 4.14 VibeGame (`VibeGame/README.md`) — ~400-500 lines

- **CLI:** run, create, --version
- **Core Concepts:** ECS (bitecs), World XML, Recipes, Plugins
- **Declarative Scene:** XML format, available elements (GLTFLoader, PlayerGLTF, Skybox, Terrain, Player, OrbitCamera, etc.)
- **GLTF Bridge:** loadGltfToScene, loadGltfAnimated, GltfAnimator, applyEquirectSkyEnvironment
- **Plugin Reference:** Full table of available plugins
- **Audio System:** AudioListener + camera, SFX/BGM
- **Examples:** hello-world, simple-rpg
- **Development:** Bun install, test, lint, format, build commands

### 4.15 Root README (`README.md`) — ~350-400 lines

- **Overview:** Monorepo description, pipeline diagram
- **Package Overview:** Table of all 15 packages with language, description, status
- **Quick Start:** Install shared, install a tool, run a command
- **Pipeline:** Full ASCII flow diagram (idea → image → 3D → texture → parts → rig → animate → game)
- **GameAssets Dream:** One-command idea-to-game
- **VibeGame Integration:** GLB handoff, declarative scene
- **Build & CI:** make check, make test, per-package commands
- **Code Style:** Python (ruff), Rust (cargo), TypeScript (prettier/eslint)
- **License & References**

## 5. Implementation Order

The READMEs will be written in dependency order (shared first, then consumers):

1. **Shared** — foundation, no tool dependencies
2. **Text2D** — first in pipeline
3. **Texture2D** — first in pipeline
4. **Skymap2D** — first in pipeline
5. **Text2Sound** — first in pipeline
6. **Text3D** — depends on Text2D images
7. **Paint3D** — depends on Text3D meshes
8. **Part3D** — depends on Paint3D output
9. **Materialize** — depends on Texture2D
10. **Terrain3D** — standalone
11. **Rigging3D** — depends on Part3D/Text3D
12. **Animator3D** — depends on Rigging3D
13. **GameDevLab** — debug tool, depends on all
14. **GameAssets** — orchestrator, depends on all
15. **VibeGame** — consumer, depends on GameAssets handoff
16. **Root README** — references all packages

Each README will be written as a complete file, replacing the existing one.

## 6. Style Guidelines

- **Self-contained:** Each README must be usable without reading other READMEs
- **Complete flag tables:** Every CLI flag with type, default, and description
- **YAML examples:** Complete, copy-pasteable examples for manifest and game.yaml
- **ASCII diagrams:** Pipeline flows where helpful
- **No redundancy:** Link to other READMEs for cross-cutting concerns rather than duplicating
- **Consistent formatting:** Same table styles, heading levels, code block conventions
- **Practical examples:** At least one example per major command

## 7. Estimated Size

| Package | Est. Lines |
|---------|-----------|
| Shared | ~160 |
| Text2D | ~200 |
| Texture2D | ~200 |
| Skymap2D | ~200 |
| Text2Sound | ~200 |
| Text3D | ~320 |
| Paint3D | ~250 |
| Part3D | ~180 |
| Rigging3D | ~200 |
| Animator3D | ~400 |
| GameDevLab | ~320 |
| Materialize | ~160 |
| Terrain3D | ~140 |
| GameAssets | ~750 |
| VibeGame | ~450 |
| Root | ~380 |
| **Total** | **~4,310** |
