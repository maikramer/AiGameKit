# Quality Presets & Game-Ready Audio — Design Spec

**Date:** 2026-04-30
**Scope:** Text2Sound + GameAssets orchestration (all tools receive `--quality`)
**Status:** In implementation

---

## 1. Motivation

The monorepo currently has no unified quality system. Each tool defines its own presets with different structures, naming conventions, and granularity. The only cross-tool system is `GenerationProfile` in GameAssets, which only tunes `steps` for Text2Sound — ignoring `cfg_scale`, `sigma_min/max`, `sampler`, and model selection. Text2Sound in particular suffers from:

1. **Low audio quality** — steps too low (4-32 across profiles), cfg/sigma/sampler untuned
2. **Limited preset coverage** — 17 presets miss creature sounds, vehicles, items, doors, abilities, etc.
3. **Trim/silence issues** — fixed 200ms buffer causes latency in short sounds, cuts ambiance tails
4. **Config complexity** — 18 CLI flags exposed with no guidance
5. **Weak GameAssets integration** — only `steps` mapped from generation profiles

The goal: **game-ready audio that "just works"** with minimal configuration across dream, batch, and standalone usage.

---

## 2. Architecture

```
game.yaml (user)         manifest.yaml (per-asset)
  generation: medium  →     category: weapon
  text2sound:                  kind: sfx_impact
    preset: explosion          generation: high  ← per-row override
    duration: 3

          ↓                           ↓
   ┌─────────────────────────────────────┐
   │        GameAssets (orchestrator)     │
   │  Reads game.yaml + manifest          │
   │  Resolves {tool, quality, category}  │
   │  Passes --quality medium --category  │
   │    weapon to each sub-tool           │
   └─────────────────────────────────────┘
          ↓
   ┌─────────────────────────────────────┐
   │   QualityEngine (gamedev-shared)    │
   │                                     │
   │  quality-profiles.yaml  ← 1 file    │
   │  asset-categories.yaml  ← 1 file    │
   │                                     │
   │  resolve(tool, quality, category)   │
   │    → {steps, cfg, sigma, sampler,   │
   │        model, duration, trim, ...}  │
   └─────────────────────────────────────┘
          ↓
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │Text2Sound│  │  Text3D  │  │  Paint3D │  ...
   │--quality │  │ --quality│  │ --quality│
   │--category│  │ --preset │  │ --category│
   └──────────┘  └──────────┘  └──────────┘
```

### Principles

- **1 YAML file** defines all quality profiles for all tools
- **1 YAML file** defines asset categories and audio kind mappings
- Each tool exposes `--quality fast|low|medium|high|highest` and optional `--category`
- `GenerationProfile` is migrated into the YAML (not duplicated)
- GameAssets becomes a thin orchestrator passing `--quality` + `--category`
- Users can always override any individual parameter (e.g., `--steps 50`)
- Audio model (music vs effects) is auto-selected based on `audio_kind`

---

## 3. Quality Profiles YAML

Location: `Shared/src/gamedev_shared/data/quality-profiles.yaml`

Five tiers map tool parameters for all tools:

| Parameter | fast | low | medium | high | highest |
|-----------|------|-----|--------|------|---------|
| **Text2D** width/height | 512² | 768² | 1024² | 1024² | 1024² |
| **Text2D** steps | 4 | 4 | 4 | 8 | 12 |
| **Text3D** preset | fast | fast | balanced | hq | hq |
| **Paint3D** max_views | 2 | 4 | 6 | 8 | 10 |
| **Paint3D** texture_size | 1024 | 2048 | 2048 | 4096 | 4096 |
| **Text2Sound** steps | 12 | 20 | 32 | 50 | 100 |
| **Text2Sound** cfg_scale | 4.0 | 5.0 | 6.0 | 7.0 | 7.0 |
| **Text2Sound** sigma_min | 0.5 | 0.4 | 0.3 | 0.2 | 0.1 |
| **Text2Sound** sampler | dpmpp-3m-sde | dpmpp-3m-sde | dpmpp-3m-sde | dpmpp-3m-sde | dpmpp-3m-sde |
| **Simplify** face_ratio | 0.25 | 0.50 | 1.00 | 2.00 | 0.00 |
| **Est. time/asset** | ~30s | ~1min | ~2min | ~5min | ~10min |

Key Text2Sound improvements over current `GenerationProfile`:
- Steps significantly increased (was 4-32, now 12-100)
- cfg_scale, sigma_min tuned per tier (not just steps)
- Sampler and sigma_max consistent; sampler overridden by `audio_kind` for effects

---

## 4. Asset Categories & Audio Kinds

Location: `Shared/src/gamedev_shared/data/asset-categories.yaml`

### Categories (14)

| Category | Target Faces | Audio Profile | Audio Kind | Duration | Trim | Rig/Animate |
|----------|-------------|---------------|------------|----------|------|-------------|
| humanoid | 32000 | music | music_loop | 30s | false | yes |
| creature | 25000 | effects | sfx_creature | 3s | true | yes |
| weapon | 2500 | effects | sfx_impact | 2s | true | — |
| prop | 5000 | effects | sfx_interact | 2s | true | — |
| chest | 2500 | effects | sfx_interact | 2s | true | — |
| door | 1200 | effects | sfx_interact | 2s | true | — |
| environment | 8000 | music | ambient_loop | 45s | false | — |
| terrain | 10000 | — | — | — | — | — |
| vegetation | 5000 | effects | ambient_one_shot | 5s | true | — |
| effects | 1200 | effects | sfx_magic | 2s | true | — |
| ui | 0 | effects | sfx_ui | 1.5s | true | — |
| vehicle | 8000 | effects | sfx_vehicle | 4s | true | — |
| building | 15000 | music | ambient_one_shot | 8s | true | — |
| item | 1200 | effects | sfx_ui | 1s | true | — |

### Audio Kinds (11)

| Kind | Model | Sampler | CFG | Trim | Trim Buffer | Loop Hint | Prompt Hint |
|------|-------|---------|-----|------|-------------|-----------|-------------|
| music_loop | music | dpmpp-3m-sde | 7.0 | false | — | yes | "seamless loop" |
| ambient_loop | music | dpmpp-3m-sde | 6.0 | false | — | yes | "seamless loop" |
| ambient_one_shot | music | dpmpp-3m-sde | 7.0 | true | 400ms | no | — |
| sfx_impact | effects | pingpong | 9.0 | true | 50ms | no | "immediate attack" |
| sfx_short | effects | pingpong | 8.0 | true | 50ms | no | — |
| sfx_ui | effects | pingpong | 10.0 | true | 30ms | no | "clean, short" |
| sfx_movement | effects | pingpong | 8.0 | true | 80ms | no | — |
| sfx_creature | effects | pingpong | 9.0 | true | 60ms | no | — |
| sfx_magic | effects | pingpong | 9.0 | true | 80ms | no | — |
| sfx_vehicle | effects | pingpong | 8.0 | true | 100ms | no | — |
| sfx_interact | effects | pingpong | 8.0 | true | 50ms | no | — |

---

## 5. Text2Sound Presets (34)

Expanded from 17 to 34, each referencing an `audio_kind`:

**Ambiences (12):** ambient, forest, ocean, rain, wind, dungeon, tavern, cave, city, desert, space, underwater
**Music (6):** battle, menu, victory, defeat, exploration, boss
**SFX Impact (5):** explosion, sword-clash, punch, gunshot, arrow
**SFX Magic (4):** magic-spell, heal, teleport, shield
**SFX Movement (4):** footsteps-stone, footsteps-grass, footsteps-wood, footsteps-water
**SFX UI (4):** ui-click, ui-confirm, ui-cancel, ui-hover
**SFX Creature (3):** creature-growl, creature-roar, creature-death

---

## 6. Audio Processing Improvements

| Feature | Before | After |
|---------|--------|-------|
| **Trim buffer** | Fixed 200ms | Per-kind: 30ms (UI) → 400ms (ambient) |
| **Fade edges** | None | 5ms fade-in, 20ms fade-out (anti-click) |
| **Normalization** | Peak only | Peak + optional RMS target |
| **Loop crossfade** | None | Configurable crossfade at end for music/ambient |
| **Prompt hints** | None | Injected per audio_kind |

---

## 7. QualityEngine API

```python
class QualityEngine:
    def resolve(self, tool, quality="medium", category=None, overrides=None) -> QualityResolution
    def list_qualities(self) -> list[str]
    def list_categories(self) -> list[str]
    def list_audio_kinds(self) -> list[str]
    def category_info(self, name) -> dict
```

Resolution priority: `overrides > category > quality_profile > defaults`

---

## 8-12. Implementation, Migration, Verification

See implementation for details. Files: 3 new, ~12 modified.
