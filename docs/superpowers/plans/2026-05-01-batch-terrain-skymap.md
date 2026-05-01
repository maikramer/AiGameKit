# Batch Terrain3D + Skymap2D + Texture2D Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Terrain3D and Skymap2D scene-level generation to `gameassets batch`, fix Texture2D torch installation, and update the debug_minimal manifest.

**Architecture:** Terrain and skymap are scene-level (single-shot) assets in batch — not per-row. They're controlled by `terrain3d:` and `skymap2d:` sections in `game.yaml`. If the profile has the appropriate section with a `prompt` field, batch runs `terrain3d generate` / `skymap2d generate` as early stages before 2D image generation. Output goes to `{output_dir}/terrain/` and `{output_dir}/sky/` respectively.

**Tech Stack:** Python 3.10+, Click CLI, Rich, subprocess invocation.

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `Shared/src/gamedev_shared/installer/registry.py:120` | Fix Texture2D `needs_pytorch` |
| Modify | `GameAssets/src/gameassets/profile.py` | Add `Terrain3DProfile` dataclass + `from_dict()` parsing + add field to `GameProfile` + add `prompt` to `Skymap2DProfile` |
| Modify | `GameAssets/src/gameassets/helpers.py` | Add terrain3d/skymap2d bin resolvers and arg builders |
| Modify | `GameAssets/src/gameassets/batch_cmd.py` | Add terrain3d/skymap2d stages + CLI flags + plan display |
| Modify | `GameAssets/debug_minimal/manifest.yaml` | Fix coin preset + add BGM entry |
| Modify | `GameAssets/debug_minimal/game.yaml` | Add terrain3d/skymap2d sections |
| Create | `GameAssets/tests/test_terrain_skymap_batch.py` | Tests for new profile parsing + helpers + batch integration |

---

## Task 1: Fix Texture2D Registry (`needs_pytorch`)

**Files:**
- Modify: `Shared/src/gamedev_shared/installer/registry.py:120`
- Test: `GameAssets/tests/test_profile.py` (no new test needed — registry is tested implicitly)

- [ ] **Step 1: Change `needs_pytorch` to `True`**

In `Shared/src/gamedev_shared/installer/registry.py`, change line 120:

```python
# Before:
        needs_pytorch=False,
# After:
        needs_pytorch=True,
```

- [ ] **Step 2: Verify no other registry tests break**

Run: `cd Shared && python -m pytest tests/ -v --tb=short`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add Shared/src/gamedev_shared/installer/registry.py
git commit -m "fix: set Texture2D needs_pytorch=True (uses FLUX/torch locally)"
```

---

## Task 2: Add `Terrain3DProfile` Dataclass + `prompt` to `Skymap2DProfile`

**Files:**
- Modify: `GameAssets/src/gameassets/profile.py`
- Create: `GameAssets/tests/test_terrain_skymap_batch.py`

### 2a. Add `Terrain3DProfile` dataclass

- [ ] **Step 1: Write the failing test**

Create `GameAssets/tests/test_terrain_skymap_batch.py`:

```python
"""Testes unitários para Terrain3DProfile, Skymap2DProfile.prompt, e helpers de batch."""

from __future__ import annotations

import pytest

from gameassets.profile import GameProfile


class TestTerrain3DProfile:
    def test_from_dict_terrain3d_minimal(self) -> None:
        p = GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "terrain3d": {
                    "prompt": "rolling hills with a river",
                },
            }
        )
        assert p.terrain3d is not None
        assert p.terrain3d.prompt == "rolling hills with a river"
        assert p.terrain3d.seed is None
        assert p.terrain3d.size is None
        assert p.terrain3d.world_size is None
        assert p.terrain3d.max_height is None
        assert p.terrain3d.quality is None

    def test_from_dict_terrain3d_full(self) -> None:
        p = GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "terrain3d": {
                    "prompt": "mountains",
                    "seed": 42,
                    "size": 1024,
                    "world_size": 256.0,
                    "max_height": 30.0,
                    "quality": "fast",
                },
            }
        )
        assert p.terrain3d is not None
        assert p.terrain3d.prompt == "mountains"
        assert p.terrain3d.seed == 42
        assert p.terrain3d.size == 1024
        assert p.terrain3d.world_size == 256.0
        assert p.terrain3d.max_height == 30.0
        assert p.terrain3d.quality == "fast"

    def test_from_dict_terrain3d_absent(self) -> None:
        p = GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
            }
        )
        assert p.terrain3d is None

    def test_from_dict_terrain3d_invalid_quality(self) -> None:
        with pytest.raises(ValueError, match="terrain3d.quality"):
            GameProfile.from_dict(
                {
                    "title": "A",
                    "genre": "B",
                    "tone": "C",
                    "style_preset": "lowpoly",
                    "terrain3d": {
                        "prompt": "hills",
                        "quality": "ultra",
                    },
                }
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd GameAssets && python -m pytest tests/test_terrain_skymap_batch.py::TestTerrain3DProfile -v`
Expected: FAIL — `Terrain3DProfile` does not exist, `p.terrain3d` is None.

- [ ] **Step 3: Add `Terrain3DProfile` dataclass**

In `GameAssets/src/gameassets/profile.py`, add after the `Skymap2DProfile` class (after line 193, before line 195):

```python
@dataclass
class Terrain3DProfile:
    """Opções para terrain3d generate (heightmap AI diffusion)."""

    prompt: str | None = None
    seed: int | None = None
    size: int | None = None
    world_size: float | None = None
    max_height: float | None = None
    quality: str | None = None
    device: str | None = None
    dtype: str | None = None
    coarse_window: int | None = None
```

- [ ] **Step 4: Add `terrain3d` field to `GameProfile`**

In the `GameProfile` dataclass (line 196), add after `skymap2d` (line 216):

```python
    terrain3d: Terrain3DProfile | None = None
```

- [ ] **Step 5: Add `terrain3d` parsing in `from_dict()`**

In `from_dict()`, add parsing between the `skymap2d` block (ends ~line 358) and the `text2sound` block (starts at line 359). Insert new parsing code:

```python
        ter3: Terrain3DProfile | None = None
        raw_ter3 = data.get("terrain3d")
        if isinstance(raw_ter3, dict):
            ter_prompt = raw_ter3.get("prompt")
            ter_prompt_s = str(ter_prompt).strip() if ter_prompt not in (None, "") else None
            ter_seed = raw_ter3.get("seed")
            ter_size = raw_ter3.get("size")
            ter_ws = raw_ter3.get("world_size")
            ter_mh = raw_ter3.get("max_height")
            ter_quality = raw_ter3.get("quality")
            ter_device = raw_ter3.get("device")
            ter_dtype = raw_ter3.get("dtype")
            ter_cw = raw_ter3.get("coarse_window")
            try:
                ter_seed_i = int(ter_seed) if ter_seed is not None else None
                ter_size_i = int(ter_size) if ter_size is not None else None
                ter_ws_f = float(ter_ws) if ter_ws is not None else None
                ter_mh_f = float(ter_mh) if ter_mh is not None else None
                ter_cw_i = int(ter_cw) if ter_cw is not None else None
            except (TypeError, ValueError) as e:
                raise ValueError(
                    "terrain3d.seed, size, world_size, max_height, coarse_window devem ser números válidos"
                ) from e
            ter_quality_s: str | None = None
            if ter_quality is not None:
                ter_quality_s = str(ter_quality).strip().lower()
                valid_qualities = ("fast", "low", "medium", "high", "highest")
                if ter_quality_s not in valid_qualities:
                    raise ValueError(
                        f"terrain3d.quality deve ser um de: {', '.join(valid_qualities)}"
                    )
            ter_device_s = str(ter_device).strip() if ter_device not in (None, "") else None
            ter_dtype_s = str(ter_dtype).strip() if ter_dtype not in (None, "") else None
            ter3 = Terrain3DProfile(
                prompt=ter_prompt_s,
                seed=ter_seed_i,
                size=ter_size_i,
                world_size=ter_ws_f,
                max_height=ter_mh_f,
                quality=ter_quality_s,
                device=ter_device_s,
                dtype=ter_dtype_s,
                coarse_window=ter_cw_i,
            )
```

- [ ] **Step 6: Add `terrain3d=ter3` to the `cls()` call**

In the `cls()` constructor call at the end of `from_dict()` (line 672-698), add `terrain3d=ter3,` after `skymap2d=sky2,`:

```python
            terrain3d=ter3,
```

- [ ] **Step 7: Run test to verify it passes**

Run: `cd GameAssets && python -m pytest tests/test_terrain_skymap_batch.py::TestTerrain3DProfile -v`
Expected: All 4 tests PASS.

- [ ] **Step 8: Run existing profile tests to verify no regression**

Run: `cd GameAssets && python -m pytest tests/test_profile.py -v`
Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add GameAssets/src/gameassets/profile.py GameAssets/tests/test_terrain_skymap_batch.py
git commit -m "feat: add Terrain3DProfile dataclass and from_dict parsing"
```

---

## Task 3: Add `prompt` to `Skymap2DProfile`

**Files:**
- Modify: `GameAssets/src/gameassets/profile.py`

### 3a. Add `prompt` field to `Skymap2DProfile`

- [ ] **Step 1: Write the failing test**

Add to `GameAssets/tests/test_terrain_skymap_batch.py`:

```python
class TestSkymap2DProfilePrompt:
    def test_from_dict_skymap2d_with_prompt(self) -> None:
        p = GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "skymap2d": {
                    "prompt": "sunset over mountains",
                    "width": 2048,
                },
            }
        )
        assert p.skymap2d is not None
        assert p.skymap2d.prompt == "sunset over mountains"
        assert p.skymap2d.width == 2048

    def test_from_dict_skymap2d_without_prompt(self) -> None:
        p = GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "skymap2d": {
                    "width": 1024,
                },
            }
        )
        assert p.skymap2d is not None
        assert p.skymap2d.prompt is None
        assert p.skymap2d.width == 1024
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd GameAssets && python -m pytest tests/test_terrain_skymap_batch.py::TestSkymap2DProfilePrompt -v`
Expected: FAIL — `Skymap2DProfile` has no `prompt` attribute.

- [ ] **Step 3: Add `prompt` to `Skymap2DProfile` dataclass**

In `GameAssets/src/gameassets/profile.py`, add `prompt` field to `Skymap2DProfile` (after line 183):

```python
@dataclass
class Skymap2DProfile:
    """Opções passadas ao CLI skymap2d generate (HF equirectangular 360°)."""

    prompt: str | None = None
    width: int | None = None
    # ... rest unchanged
```

- [ ] **Step 4: Parse `prompt` in the skymap2d `from_dict()` section**

In `from_dict()`, in the `skymap2d` parsing block (around lines 322-358), add prompt parsing. After `raw_sky2 = data.get("skymap2d")` and `if isinstance(raw_sky2, dict):`, add:

```python
            sky_prompt = raw_sky2.get("prompt")
            sky_prompt_s = str(sky_prompt).strip() if sky_prompt not in (None, "") else None
```

Then include `prompt=sky_prompt_s,` in the `Skymap2DProfile(...)` constructor call (line 348).

- [ ] **Step 5: Run test to verify it passes**

Run: `cd GameAssets && python -m pytest tests/test_terrain_skymap_batch.py::TestSkymap2DProfilePrompt -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add GameAssets/src/gameassets/profile.py GameAssets/tests/test_terrain_skymap_batch.py
git commit -m "feat: add prompt field to Skymap2DProfile for batch support"
```

---

## Task 4: Add Terrain3D / Skymap2D Helpers

**Files:**
- Modify: `GameAssets/src/gameassets/helpers.py`

### 4a. Add helpers

- [ ] **Step 1: Write the failing test**

Add to `GameAssets/tests/test_terrain_skymap_batch.py`:

```python
from gameassets.helpers import (
    _append_terrain3d_profile_args,
    _append_skymap2d_profile_args,
    _resolve_terrain3d_bin,
    _resolve_skymap2d_bin,
)
from gameassets.profile import Terrain3DProfile, Skymap2DProfile


class TestTerrain3DHelpers:
    def test_terrain3d_argv_minimal(self) -> None:
        ter = Terrain3DProfile(prompt="hills")
        argv: list[str] = []
        _append_terrain3d_profile_args(ter, argv)
        assert "--prompt" in argv
        assert "hills" in argv
        assert "--seed" not in argv

    def test_terrain3d_argv_full(self) -> None:
        ter = Terrain3DProfile(
            prompt="mountains",
            seed=42,
            size=1024,
            world_size=256.0,
            max_height=30.0,
            quality="fast",
            coarse_window=4,
        )
        argv: list[str] = []
        _append_terrain3d_profile_args(ter, argv)
        assert "--seed" in argv and "42" in argv
        assert "--size" in argv and "1024" in argv
        assert "--world-size" in argv and "256.0" in argv
        assert "--max-height" in argv and "30.0" in argv
        assert "--quality" in argv and "fast" in argv
        assert "--coarse-window" in argv and "4" in argv

    def test_terrain3d_argv_no_prompt(self) -> None:
        ter = Terrain3DProfile()
        argv: list[str] = []
        _append_terrain3d_profile_args(ter, argv)
        assert "--prompt" not in argv

    def test_terrain3d_argv_device_dtype(self) -> None:
        ter = Terrain3DProfile(device="cuda:0", dtype="fp16")
        argv: list[str] = []
        _append_terrain3d_profile_args(ter, argv)
        assert "--device" in argv and "cuda:0" in argv
        assert "--dtype" in argv and "fp16" in argv


class TestSkymap2DHelpers:
    def test_skymap2d_argv_prompt(self) -> None:
        sky = Skymap2DProfile(prompt="sunset", width=2048, steps=20)
        argv: list[str] = []
        _append_skymap2d_profile_args(sky, argv)
        assert "-W" in argv and "2048" in argv
        assert "-s" in argv and "20" in argv

    def test_skymap2d_argv_empty(self) -> None:
        sky = Skymap2DProfile()
        argv: list[str] = []
        _append_skymap2d_profile_args(sky, argv)
        assert argv == []

    def test_skymap2d_argv_full(self) -> None:
        sky = Skymap2DProfile(
            width=4096,
            height=2048,
            steps=30,
            guidance_scale=5.0,
            negative_prompt="blurry",
            preset="sunset",
            cfg_scale=7.5,
            lora_strength=0.8,
            model_id="some-model",
        )
        argv: list[str] = []
        _append_skymap2d_profile_args(sky, argv)
        assert "-W" in argv and "4096" in argv
        assert "-H" in argv and "2048" in argv
        assert "-s" in argv and "30" in argv
        assert "-g" in argv and "5.0" in argv
        assert "-n" in argv and "blurry" in argv
        assert "-p" in argv and "sunset" in argv
        assert "--cfg-scale" in argv and "7.5" in argv
        assert "--lora-strength" in argv and "0.8" in argv
        assert "-m" in argv and "some-model" in argv
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd GameAssets && python -m pytest tests/test_terrain_skymap_batch.py::TestTerrain3DHelpers tests/test_terrain_skymap_batch.py::TestSkymap2DHelpers -v`
Expected: FAIL — imports don't exist.

- [ ] **Step 3: Add imports and helper functions to `helpers.py`**

Add `Terrain3DProfile` and `Skymap2DProfile` to the imports from `.profile` (around line 13):

```python
from .profile import (
    GameProfile,
    Skymap2DProfile,
    Terrain3DProfile,
    Text2SoundProfile,
    Texture2DProfile,
    load_profile,
)
```

Add these functions after `_resolve_materialize_bin_texture2d()` (after line 227):

```python
def _resolve_terrain3d_bin() -> str:
    """Resolve terrain3d binary (env override or PATH)."""
    return resolve_binary("TERRAIN3D_BIN", "terrain3d")


def _resolve_skymap2d_bin() -> str:
    """Resolve skymap2d binary (env override or PATH)."""
    return resolve_binary("SKYMAP2D_BIN", "skymap2d")


def _append_terrain3d_profile_args(ter: Terrain3DProfile, argv: list[str]) -> None:
    """Profile args for terrain3d generate (scene-level heightmap)."""
    if ter.prompt:
        argv.extend(["--prompt", ter.prompt])
    if ter.seed is not None:
        argv.extend(["--seed", str(ter.seed)])
    if ter.size is not None:
        argv.extend(["--size", str(ter.size)])
    if ter.world_size is not None:
        argv.extend(["--world-size", str(ter.world_size)])
    if ter.max_height is not None:
        argv.extend(["--max-height", str(ter.max_height)])
    if ter.quality:
        argv.extend(["--quality", ter.quality])
    if ter.device:
        argv.extend(["--device", ter.device])
    if ter.dtype:
        argv.extend(["--dtype", ter.dtype])
    if ter.coarse_window is not None:
        argv.extend(["--coarse-window", str(ter.coarse_window)])


def _append_skymap2d_profile_args(sky: Skymap2DProfile, argv: list[str]) -> None:
    """Profile args for skymap2d generate (scene-level equirect sky)."""
    if sky.width is not None:
        argv.extend(["-W", str(sky.width)])
    if sky.height is not None:
        argv.extend(["-H", str(sky.height)])
    if sky.steps is not None:
        argv.extend(["-s", str(sky.steps)])
    if sky.guidance_scale is not None:
        argv.extend(["-g", str(sky.guidance_scale)])
    if sky.negative_prompt:
        argv.extend(["-n", sky.negative_prompt])
    if sky.preset and sky.preset.lower() != "none":
        argv.extend(["-p", sky.preset])
    if sky.cfg_scale is not None:
        argv.extend(["--cfg-scale", str(sky.cfg_scale)])
    if sky.lora_strength is not None:
        argv.extend(["--lora-strength", str(sky.lora_strength)])
    if sky.model_id:
        argv.extend(["-m", sky.model_id])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd GameAssets && python -m pytest tests/test_terrain_skymap_batch.py::TestTerrain3DHelpers tests/test_terrain_skymap_batch.py::TestSkymap2DHelpers -v`
Expected: All PASS.

- [ ] **Step 5: Run existing tests**

Run: `cd GameAssets && python -m pytest tests/test_cli_helpers.py tests/test_profile.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add GameAssets/src/gameassets/helpers.py GameAssets/tests/test_terrain_skymap_batch.py
git commit -m "feat: add terrain3d/skymap2d bin resolvers and profile arg helpers"
```

---

## Task 5: Add Terrain3D + Skymap2D Stages to `batch_cmd.py`

**Files:**
- Modify: `GameAssets/src/gameassets/batch_cmd.py`

This is the largest task. Changes are needed in:
1. CLI options (add `--no-terrain`, `--no-skymap`)
2. Function signature (`batch_cmd()`)
3. Binary resolution (add `terrain3d_bin`, `skymap2d_bin`)
4. Plan display "Ordem" string
5. Dashboard path (`_batch_fn`) — add terrain + skymap stages
6. Progress-bar path — add terrain + skymap stages

### 5a. CLI options

- [ ] **Step 1: Add `--no-terrain` and `--no-skymap` options**

After the `--no-collision` option (line 194) and before `--profile-tools` (line 196), add:

```python
@click.option(
    "--no-terrain",
    is_flag=True,
    default=False,
    help="Skip terrain generation even if terrain3d is configured.",
)
@click.option(
    "--no-skymap",
    is_flag=True,
    default=False,
    help="Skip skymap generation even if skymap2d is configured.",
)
```

- [ ] **Step 2: Add `no_terrain` and `no_skymap` to `batch_cmd()` parameters**

In the `batch_cmd()` function signature (line 234), add `no_terrain: bool, no_skymap: bool,` parameters alongside the other flags.

### 5b. Binary resolution

- [ ] **Step 3: Add terrain3d and skymap2d binary resolution**

After the `text2sound_bin` resolution block (around line 387), add:

```python
    # --- terrain3d / skymap2d (scene-level) ---
    has_terrain = profile.terrain3d is not None and profile.terrain3d.prompt
    has_skymap = profile.skymap2d is not None and profile.skymap2d.prompt

    terrain3d_bin: str | None = None
    if has_terrain and not no_terrain:
        try:
            terrain3d_bin = resolve_binary("TERRAIN3D_BIN", "terrain3d")
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

    skymap2d_bin: str | None = None
    if has_skymap and not no_skymap:
        try:
            skymap2d_bin = resolve_binary("SKYMAP2D_BIN", "skymap2d")
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e
```

Also add imports at the top of the file — add to the imports from `helpers.py`:

```python
from .helpers import (
    # ... existing imports ...
    _append_terrain3d_profile_args,
    _append_skymap2d_profile_args,
    _resolve_terrain3d_bin,
    _resolve_skymap2d_bin,
)
```

### 5c. Plan display "Ordem" update

- [ ] **Step 4: Update the "Ordem" string to include terrain + skymap**

In the plan display section (lines 427-472), prepend terrain/skymap stages. The order becomes:

```
Terrain3D (heightmap) → Skymap2D (sky) → 2D Images → Materialize → Text2Sound → Text3D → ...
```

In each of the three branches (A: skip_text2d, B: mixed, C: fallback), add terrain/skymap to the beginning of the order string when applicable. Example for Branch B:

```python
        order_parts = []
        if has_terrain and not no_terrain:
            order_parts.append("Terrain3D (heightmap)")
        if has_skymap and not no_skymap:
            order_parts.append("Skymap2D (sky)")
        if any_texture2d_row and any_text2d_row:
            order_parts.append("Geração 2D por linha (text2d e/ou texture2d)")
        elif any_texture2d_row:
            order_parts.append("Texture2D (todas as linhas)")
        else:
            order_parts.append("Text2D (todas as linhas)")
        # ... rest unchanged, just prepend order_parts
```

Add `→` between order_parts and append the existing stages. Display using `meta.add_row("Ordem", " → ".join(order_parts))`.

### 5d. Dashboard path (`_batch_fn`)

- [ ] **Step 5: Add terrain stage to dashboard path**

In `_batch_fn(dash)` (starts at line 873), **before** the 2D image generation phase, add a terrain stage block. Find the appropriate insertion point (right after initial setup and before 2D image phase):

```python
        # --- Terrain3D (scene-level) ---
        if has_terrain and terrain3d_bin and not no_terrain:
            with _phase("Terrain3D"):
                dash.set_step("Terrain3D — heightmap generation")
                ter = profile.terrain3d
                ter_dir = Path(profile.output_dir) / "terrain"
                ter_dir.mkdir(parents=True, exist_ok=True)
                ter_argv = [terrain3d_bin, "generate"]
                if ter:
                    _append_terrain3d_profile_args(ter, ter_argv)
                ter_argv.extend([
                    "--output", str(ter_dir / "heightmap.png"),
                    "--metadata", str(ter_dir / "terrain.json"),
                    "--quiet",
                ])
                dash.log_cmd(ter_argv)
                rec_t = {"phase": "terrain3d", "argv": ter_argv}
                if dry_run:
                    append_log({**rec_t, "dry_run": True})
                else:
                    rc_t = subprocess.call(ter_argv)
                    ok_t = rc_t == 0
                    append_log({**rec_t, "ok": ok_t, "exit": rc_t})
                    if not ok_t:
                        _step("Terrain3D", ok=False, detail=f"exit {rc_t}")
                        if fail_fast:
                            return
                    else:
                        _step("Terrain3D", detail=f"{ter_dir / 'heightmap.png'}")
```

- [ ] **Step 6: Add skymap stage to dashboard path**

Immediately after the terrain block, add:

```python
        # --- Skymap2D (scene-level) ---
        if has_skymap and skymap2d_bin and not no_skymap:
            with _phase("Skymap2D"):
                dash.set_step("Skymap2D — equirectangular sky")
                sky = profile.skymap2d
                sky_dir = Path(profile.output_dir) / "sky"
                sky_dir.mkdir(parents=True, exist_ok=True)
                sky_argv = [skymap2d_bin, "generate", sky.prompt or ""]
                if sky:
                    _append_skymap2d_profile_args(sky, sky_argv)
                sky_argv.extend(["-o", str(sky_dir / "sky.png")])
                dash.log_cmd(sky_argv)
                rec_sky = {"phase": "skymap2d", "argv": sky_argv}
                if dry_run:
                    append_log({**rec_sky, "dry_run": True})
                else:
                    rc_sky = subprocess.call(sky_argv)
                    ok_sky = rc_sky == 0
                    append_log({**rec_sky, "ok": ok_sky, "exit": rc_sky})
                    if not ok_sky:
                        _step("Skymap2D", ok=False, detail=f"exit {rc_sky}")
                        if fail_fast:
                            return
                    else:
                        _step("Skymap2D", detail=f"{sky_dir / 'sky.png'}")
```

### 5e. Progress-bar path

- [ ] **Step 7: Add terrain + skymap stages to progress-bar path**

The progress-bar path is the `else` branch (when `--no-dashboard` or `--plain`). Find the start of 2D image generation in this path and insert terrain/skymap blocks before it, following the same pattern as the dashboard but using `console.print()` instead of `dash.set_step()`:

```python
    # --- Terrain3D (scene-level, progress-bar path) ---
    if has_terrain and terrain3d_bin and not no_terrain:
        console.print("[bold]Terrain3D[/bold] — heightmap generation")
        ter = profile.terrain3d
        ter_dir = Path(profile.output_dir) / "terrain"
        ter_dir.mkdir(parents=True, exist_ok=True)
        ter_argv = [terrain3d_bin, "generate"]
        if ter:
            _append_terrain3d_profile_args(ter, ter_argv)
        ter_argv.extend([
            "--output", str(ter_dir / "heightmap.png"),
            "--metadata", str(ter_dir / "terrain.json"),
            "--quiet",
        ])
        if dry_run:
            console.print(f"[dim]$ {' '.join(ter_argv)}[/dim]")
            append_log({"phase": "terrain3d", "argv": ter_argv, "dry_run": True})
        else:
            rc_t = subprocess.call(ter_argv)
            append_log({"phase": "terrain3d", "argv": ter_argv, "ok": rc_t == 0, "exit": rc_t})
            if rc_t != 0:
                console.print(f"[red]Terrain3D failed (exit {rc_t})[/red]")
                if fail_fast:
                    raise SystemExit(1)

    # --- Skymap2D (scene-level, progress-bar path) ---
    if has_skymap and skymap2d_bin and not no_skymap:
        console.print("[bold]Skymap2D[/bold] — equirectangular sky")
        sky = profile.skymap2d
        sky_dir = Path(profile.output_dir) / "sky"
        sky_dir.mkdir(parents=True, exist_ok=True)
        sky_argv = [skymap2d_bin, "generate", sky.prompt or ""]
        if sky:
            _append_skymap2d_profile_args(sky, sky_argv)
        sky_argv.extend(["-o", str(sky_dir / "sky.png")])
        if dry_run:
            console.print(f"[dim]$ {' '.join(sky_argv)}[/dim]")
            append_log({"phase": "skymap2d", "argv": sky_argv, "dry_run": True})
        else:
            rc_sky = subprocess.call(sky_argv)
            append_log({"phase": "skymap2d", "argv": sky_argv, "ok": rc_sky == 0, "exit": rc_sky})
            if rc_sky != 0:
                console.print(f"[red]Skymap2D failed (exit {rc_sky})[/red]")
                if fail_fast:
                    raise SystemExit(1)
```

- [ ] **Step 8: Run existing batch tests**

Run: `cd GameAssets && python -m pytest tests/ -v --tb=short`
Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add GameAssets/src/gameassets/batch_cmd.py
git commit -m "feat: add terrain3d and skymap2d scene-level stages to batch"
```

---

## Task 6: Update `debug_minimal/game.yaml` — Add Terrain3D + Skymap2D Sections

**Files:**
- Modify: `GameAssets/debug_minimal/game.yaml`

- [ ] **Step 1: Add `terrain3d:` and `skymap2d:` sections**

In `GameAssets/debug_minimal/game.yaml`, add after the `skymap2d:` comment or before the `text3d:` section:

```yaml
terrain3d:
  prompt: "colinas verdes suaves com rio"
  seed: 42
  size: 1024
  world_size: 256.0
  max_height: 20.0
  quality: fast

skymap2d:
  prompt: "céu azul claro com nuvens brancas, sunset"
  width: 1024
  height: 512
  steps: 10
```

Note: The `skymap2d:` block already exists in the original game.yaml without `prompt`. Add `prompt` to it and adjust other params for fast generation.

- [ ] **Step 2: Verify the YAML parses correctly**

Run: `cd GameAssets && python -c "from gameassets.profile import load_profile; from pathlib import Path; p = load_profile(Path('debug_minimal/game.yaml')); print(f'terrain={p.terrain3d} sky={p.skymap2d}')"`
Expected: Both profiles print with correct values.

- [ ] **Step 3: Commit**

```bash
git add GameAssets/debug_minimal/game.yaml
git commit -m "chore: add terrain3d/skymap2d sections to debug_minimal game.yaml"
```

---

## Task 7: Update `debug_minimal/manifest.yaml` — Fix Coin + Add BGM

**Files:**
- Modify: `GameAssets/debug_minimal/manifest.yaml`

- [ ] **Step 1: Fix coin preset and add BGM entry**

Change `sfx_coin_pickup` preset from `ui-confirm` to `coin-pickup`, and add a BGM entry:

```yaml
  - id: sfx_coin_pickup
    idea: "som breve de moeda a tilintar, pickup"
    kind: prop
    pipeline: [audio]
    audio:
      duration: 0.5
      profile: effects
      preset: coin-pickup
      trim: true

  - id: bgm_exploration
    idea: "música ambiente de exploração, calma, loop"
    kind: music
    pipeline: [audio]
    audio:
      duration: 15.0
      profile: music
      preset: exploration
      trim: true
```

- [ ] **Step 2: Verify manifest loads**

Run: `cd GameAssets && python -c "from gameassets.manifest import load_manifest; from pathlib import Path; rows = load_manifest(Path('debug_minimal/manifest.yaml')); print(f'{len(rows)} rows: {[r.id for r in rows]}')"`
Expected: `5 rows: ['wooden_crate', 'sfx_coin_pickup', 'goblin_warrior', 'stone_tile_from_texture', 'bgm_exploration']`

- [ ] **Step 3: Commit**

```bash
git add GameAssets/debug_minimal/manifest.yaml
git commit -m "fix: coin preset to coin-pickup, add BGM exploration entry"
```

---

## Task 8: Reinstall Texture2D + Run Full Test Suite

**Files:**
- No code changes — verification only.

- [ ] **Step 1: Run full test suite**

Run: `cd GameAssets && python -m pytest tests/ -v --tb=short`
Expected: All pass.

- [ ] **Step 2: Run lint + format check**

Run: `cd /home/maikeu/GitClones/GameDev && ruff check GameAssets/ && ruff format --check GameAssets/`
Expected: Clean (no errors).

- [ ] **Step 3: Verify Texture2D reinstall works**

Run: `python3 -m gamedev_shared.installer.unified texture2d --force 2>&1 | tail -5`
Expected: Installation succeeds. Verify torch is installed: `Texture2D/.venv/bin/python -c "import torch; print(torch.__version__)"`

- [ ] **Step 4: Commit (if any formatting fixes needed)**

```bash
git add -A
git commit -m "style: ruff format after terrain/skymap batch integration"
```

---

## Task 9: Re-run Batch (Manual Verification)

**Files:**
- No code changes.

- [ ] **Step 1: Dry-run the batch**

Run: `cd GameAssets/debug_minimal && gameassets batch --profile game.yaml --manifest manifest.yaml --dry-run --plain`
Expected: Plan shows terrain3d + skymap2d stages, then 2D images → audio → 3D → parts → rig → animate.

- [ ] **Step 2: Execute the batch (if user requests)**

Run: `cd GameAssets/debug_minimal && gameassets batch --profile game.yaml --manifest manifest.yaml --plain`
Expected: All stages execute (or gracefully skip if tools not installed on this machine).
