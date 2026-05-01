# Seamless Loop Equal-Power Crossfade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add equal-power crossfade post-processing to Text2Sound so that BGM loops (`music_loop`, `ambient_loop`) transition seamlessly between end and start.

**Architecture:** New function `apply_seamless_loop_crossfade` in `audio_processor.py` applies cos²/sin² crossfade between the last N ms and first N ms of the audio. Triggered automatically via QualityEngine when `audio_kind` has `loop_hint: true`. Replaces `apply_edge_fade` when active. The `crossfade_ms` parameter is configurable per quality tier.

**Tech Stack:** Python, PyTorch (tensor ops only, no new deps), YAML config in Shared.

---

### Task 1: Add `apply_seamless_loop_crossfade` to `audio_processor.py`

**Files:**
- Modify: `Text2Sound/src/text2sound/audio_processor.py` (new function after `apply_edge_fade`)
- Test: `Text2Sound/tests/test_audio_processor.py`

- [ ] **Step 1: Write failing tests for `apply_seamless_loop_crossfade`**

Add to `Text2Sound/tests/test_audio_processor.py` — new import and test class:

```python
# Add to imports at top:
from text2sound.audio_processor import (
    DEFAULT_FORMAT,
    SUPPORTED_FORMATS,
    apply_edge_fade,
    apply_seamless_loop_crossfade,  # NEW
    peak_normalize,
    save_audio,
    to_int16,
    trim_silence,
)
```

Add new test class after `TestApplyEdgeFade`:

```python
class TestApplySeamlessLoopCrossfade:
    def test_output_shape_matches_input(self):
        sr = 44100
        audio = torch.randn(2, sr * 5)  # 5 seconds stereo
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == audio.shape

    def test_equal_power_property(self):
        """cos^2 + sin^2 should equal ~1.0 for all points."""
        sr = 44100
        audio = torch.randn(2, sr * 5)
        n = int(sr * 500.0 / 1000)
        t = torch.linspace(0, torch.pi / 2, n)
        fade_out = torch.cos(t) ** 2
        fade_in = torch.sin(t) ** 2
        energy = fade_out + fade_in
        assert torch.allclose(energy, torch.ones_like(energy), atol=1e-6)

    def test_center_unchanged(self):
        """Samples outside crossfade zone should be identical to input."""
        sr = 44100
        audio = torch.randn(2, sr * 5)
        n = int(sr * 500.0 / 1000)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        # Middle section (excluding last n samples which are crossfaded)
        assert torch.equal(result[:, :-n], audio[:, :-n])

    def test_works_with_mono(self):
        sr = 44100
        audio = torch.randn(1, sr * 5)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == audio.shape

    def test_short_audio_crossfade_clamped(self):
        """Audio shorter than crossfade_ms should clamp crossfade to half length."""
        sr = 44100
        audio = torch.randn(2, 100)  # very short
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == audio.shape

    def test_does_not_modify_original(self):
        sr = 44100
        audio = torch.randn(2, sr * 5)
        original = audio.clone()
        apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert torch.equal(audio, original)

    def test_crossfade_500ms_stereo(self):
        """Full integration: 500ms crossfade on stereo audio, verify smooth transition."""
        sr = 44100
        # Create audio with distinct start and end
        audio = torch.randn(2, sr * 5)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        # The last 500ms should be a mix (not identical to input)
        n = int(sr * 500.0 / 1000)
        assert not torch.equal(result[:, -n:], audio[:, -n:])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Text2Sound && python -m pytest tests/test_audio_processor.py::TestApplySeamlessLoopCrossfade -v`
Expected: FAIL — `ImportError: cannot import name 'apply_seamless_loop_crossfade'`

- [ ] **Step 3: Implement `apply_seamless_loop_crossfade`**

Add to `Text2Sound/src/text2sound/audio_processor.py` after `apply_edge_fade` (after line 115):

```python
def apply_seamless_loop_crossfade(
    audio: torch.Tensor,
    sample_rate: int,
    crossfade_ms: float = 500.0,
) -> torch.Tensor:
    """Apply equal-power crossfade between the end and start of audio for seamless looping.

    Blends the last ``crossfade_ms`` milliseconds with the first ``crossfade_ms``
    milliseconds using cos²/sin² curves, preserving RMS energy during the transition.
    The output has the same duration as the input — the crossfaded region replaces
    the tail of the audio.

    Args:
        audio: Tensor (channels, samples) float.
        sample_rate: Sample rate in Hz.
        crossfade_ms: Crossfade duration in milliseconds.

    Returns:
        Tensor with crossfade applied (channels, samples), same length as input.
    """
    total_samples = audio.shape[-1]
    if total_samples == 0:
        return audio

    n = int(sample_rate * crossfade_ms / 1000)
    # Clamp crossfade to at most half the audio length
    n = min(n, total_samples // 2)
    if n < 2:
        return audio.clone()

    t = torch.linspace(0, torch.pi / 2, n, device=audio.device, dtype=audio.dtype)
    fade_out = torch.cos(t) ** 2  # (n,)
    fade_in = torch.sin(t) ** 2   # (n,)

    tail = audio[:, -n:]   # last n samples
    head = audio[:, :n]    # first n samples

    # Broadcast: (channels, n) * (n,) → (channels, n)
    crossfaded = tail * fade_out + head * fade_in

    result = audio.clone()
    result[:, -n:] = crossfaded
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Text2Sound && python -m pytest tests/test_audio_processor.py::TestApplySeamlessLoopCrossfade -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add Text2Sound/src/text2sound/audio_processor.py Text2Sound/tests/test_audio_processor.py
git commit -m "feat(text2sound): add apply_seamless_loop_crossfade with equal-power curves"
```

---

### Task 2: Extend `save_audio` with seamless loop support

**Files:**
- Modify: `Text2Sound/src/text2sound/audio_processor.py:118-179` (`save_audio` function)
- Test: `Text2Sound/tests/test_audio_processor.py`

- [ ] **Step 1: Write failing tests for `save_audio` with seamless loop**

Add to `Text2Sound/tests/test_audio_processor.py` in `TestSaveAudio`:

```python
    def test_seamless_loop_applies_crossfade(self, tmp_path):
        """seamless_loop=True should apply crossfade instead of edge fade."""
        audio = torch.randn(2, 44100)
        out = tmp_path / "loop"
        result = save_audio(audio, 44100, out, seamless_loop=True, crossfade_ms=500.0)
        assert result.exists()

    def test_seamless_loop_false_uses_edge_fade(self, tmp_path):
        """seamless_loop=False (default) should use edge fade as before."""
        audio = torch.randn(2, 44100)
        out = tmp_path / "no_loop"
        result = save_audio(audio, 44100, out)
        assert result.exists()

    def test_seamless_loop_metadata(self, tmp_path):
        """Metadata should include seamless_loop and crossfade_ms."""
        audio = torch.randn(2, 44100)
        meta = {"prompt": "test"}
        out = tmp_path / "loop_meta"
        result = save_audio(
            audio, 44100, out,
            seamless_loop=True,
            crossfade_ms=500.0,
            metadata=meta,
        )
        meta_path = result.with_suffix(result.suffix + ".json")
        data = json.loads(meta_path.read_text())
        assert data["seamless_loop"] is True
        assert data["crossfade_ms"] == 500.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd Text2Sound && python -m pytest tests/test_audio_processor.py::TestSaveAudio::test_seamless_loop_applies_crossfade -v`
Expected: FAIL — `TypeError: save_audio() got an unexpected keyword argument 'seamless_loop'`

- [ ] **Step 3: Modify `save_audio` signature and logic**

In `Text2Sound/src/text2sound/audio_processor.py`, modify the `save_audio` function:

Replace the function signature (lines 118-130):

```python
def save_audio(
    audio: torch.Tensor,
    sample_rate: int,
    output_path: Path,
    fmt: str = DEFAULT_FORMAT,
    as_int16: bool = True,
    normalize: bool = True,
    trim: bool = False,
    metadata: dict[str, Any] | None = None,
    trim_buffer_ms: int = 200,
    trim_threshold_db: float = -60.0,
    apply_fade: bool = True,
    seamless_loop: bool = False,
    crossfade_ms: float = 500.0,
) -> Path:
```

Add to the docstring Args section:

```
        seamless_loop: Apply equal-power crossfade for seamless loop playback.
        crossfade_ms: Crossfade duration in milliseconds (only used when seamless_loop=True).
```

Replace the post-processing block (lines 161-162):

Old:
```python
    if apply_fade:
        audio = apply_edge_fade(audio, sample_rate)
```

New:
```python
    if seamless_loop:
        audio = apply_seamless_loop_crossfade(audio, sample_rate, crossfade_ms=crossfade_ms)
    elif apply_fade:
        audio = apply_edge_fade(audio, sample_rate)
```

Add metadata recording before the JSON write block (before line 173):

```python
    if metadata:
        if seamless_loop:
            metadata["seamless_loop"] = True
            metadata["crossfade_ms"] = crossfade_ms
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd Text2Sound && python -m pytest tests/test_audio_processor.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add Text2Sound/src/text2sound/audio_processor.py Text2Sound/tests/test_audio_processor.py
git commit -m "feat(text2sound): extend save_audio with seamless_loop crossfade option"
```

---

### Task 3: Add `crossfade_ms` to `audio_kinds` and quality profiles in Shared YAML

**Files:**
- Modify: `Shared/src/gamedev_shared/data/asset-categories.yaml` (lines 184-202)
- Modify: `Shared/src/gamedev_shared/data/quality-profiles.yaml` (text2sound sections)

- [ ] **Step 1: Add `crossfade_ms` to `music_loop` and `ambient_loop` in asset-categories.yaml**

In `Shared/src/gamedev_shared/data/asset-categories.yaml`, add `crossfade_ms: 500` to both loop kinds:

For `music_loop` (after line 192, add):
```yaml
    crossfade_ms: 500
```

For `ambient_loop` (after line 202, add):
```yaml
    crossfade_ms: 500
```

Result for `music_loop`:
```yaml
  music_loop:
    label: "Music Loop"
    model: music
    sampler: dpmpp-3m-sde
    cfg_scale_default: 7.0
    trim_default: false
    trim_buffer_ms: 0
    loop_hint: true
    crossfade_ms: 500
    prompt_hint: "seamless loop, consistent volume throughout"
```

Result for `ambient_loop`:
```yaml
  ambient_loop:
    label: "Ambient Loop"
    model: music
    sampler: dpmpp-3m-sde
    cfg_scale_default: 6.0
    trim_default: false
    trim_buffer_ms: 0
    loop_hint: true
    crossfade_ms: 500
    prompt_hint: "seamless loop, immersive atmosphere"
```

- [ ] **Step 2: Add `crossfade_ms` to each quality tier's `text2sound` section in quality-profiles.yaml**

Add `crossfade_ms` field to each tier's `text2sound:` block:

- `fast` (after line 46): `crossfade_ms: 300`
- `low` (after line 96): `crossfade_ms: 400`
- `medium` (after line 145): `crossfade_ms: 500`
- `high` (after line 194): `crossfade_ms: 600`
- `highest` (after line 243): `crossfade_ms: 800`

Example for `fast`:
```yaml
    text2sound:
      steps: 12
      cfg_scale: 4.0
      sigma_min: 0.5
      sigma_max: 500.0
      sampler: dpmpp-3m-sde
      crossfade_ms: 300
```

- [ ] **Step 3: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('Shared/src/gamedev_shared/data/asset-categories.yaml')); yaml.safe_load(open('Shared/src/gamedev_shared/data/quality-profiles.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add Shared/src/gamedev_shared/data/asset-categories.yaml Shared/src/gamedev_shared/data/quality-profiles.yaml
git commit -m "feat(shared): add crossfade_ms to loop audio_kinds and quality profiles"
```

---

### Task 4: Wire QualityEngine `crossfade_ms` + `loop_hint` into CLI

**Files:**
- Modify: `Text2Sound/src/text2sound/cli.py` (lines 334-374 and 490-529)

- [ ] **Step 1: Extract `crossfade_ms` and `loop_hint` from QualityEngine resolution in `generate_cmd`**

In `Text2Sound/src/text2sound/cli.py`, after the existing variables at line 336, add:

```python
    seamless_loop: bool = False
    crossfade_ms: float = 500.0
```

Inside the `try:` QualityEngine block (after line 371, before `except`), add:

```python
                # Seamless loop from audio_kind
                if quality_audio_kind:
                    try:
                        kind_info = qe.audio_kind_info(quality_audio_kind)
                        if kind_info.get("loop_hint"):
                            seamless_loop = True
                            # crossfade_ms: quality profile override > audio_kind default
                            if "crossfade_ms" in resolved.params:
                                crossfade_ms = float(resolved.params["crossfade_ms"])
                            elif "crossfade_ms" in kind_info:
                                crossfade_ms = float(kind_info["crossfade_ms"])
                    except KeyError:
                        pass
```

- [ ] **Step 2: Pass `seamless_loop` and `crossfade_ms` to `save_audio` in `generate_cmd`**

In the `save_audio` call inside `generate_cmd` (around line 520), add the new parameters:

```python
                with profile_span("save"):
                    saved = save_audio(
                        audio=result.audio,
                        sample_rate=result.sample_rate,
                        output_path=out_path,
                        fmt=fmt,
                        trim=trim,
                        metadata=metadata,
                        trim_buffer_ms=trim_buffer_ms,
                        trim_threshold_db=trim_threshold_db,
                        seamless_loop=seamless_loop,
                        crossfade_ms=crossfade_ms,
                    )
```

- [ ] **Step 3: Add seamless loop info to the display table**

After the `if quality_audio_kind:` block in the table setup (around line 423), add:

```python
    if seamless_loop:
        table.add_row("[bold]Seamless Loop[/bold]", f"[green]ON[/green] ({crossfade_ms:.0f}ms crossfade)")
```

- [ ] **Step 4: Add to profiler params**

In the `_prof_params` dict (around line 431), add:

```python
        "seamless_loop": seamless_loop,
        "crossfade_ms": crossfade_ms,
```

- [ ] **Step 5: Verify CLI smoke test still passes**

Run: `cd Text2Sound && python -m pytest tests/test_cli_smoke.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add Text2Sound/src/text2sound/cli.py
git commit -m "feat(text2sound): wire QualityEngine seamless loop into CLI pipeline"
```

---

### Task 5: Run full test suite and lint

**Files:** None (verification only)

- [ ] **Step 1: Run Text2Sound tests**

Run: `cd Text2Sound && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 2: Run Shared tests (QualityEngine may read new YAML fields)**

Run: `cd Shared && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Run lint and format check**

Run: `ruff check Text2Sound/ Shared/src/gamedev_shared/data/ && ruff format --check Text2Sound/`
Expected: No errors

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -A && git commit -m "chore: lint/format fixes for seamless loop feature"
```
