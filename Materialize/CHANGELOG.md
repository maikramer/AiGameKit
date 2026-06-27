# Changelog

## [2.0.0] - 2026-06-26

### ⚠️ Breaking changes

These changes may require migration for callers that introspect the binary's
behaviour. Most users on default paths are unaffected.

| # | Change | Migration |
|---|---|---|
| BC1 | Exit codes are now granular (0 success · 1 generic · 2 input not found · 3 unsupported input format · 4 GPU error · 5 I/O error · 6 image too large). | Prefer checking `!= 0` rather than `== 1`. Map specific codes if you need granular error handling. |
| BC2 | `PresetParams` layout bumped from 48 → 64 bytes. The `struct Params` block in every WGSL shader was updated to match. | Internal change; no CLI impact. |
| BC3 | New `auto` value for `--preset`. | Additive. |
| BC4 | 12 new presets: `concrete, leather, marble, sand, foliage, plaster, asphalt, brick, ice, snow, lava, water`. | Additive. |
| BC5 | `--quality` still accepts `0..=100`; values of `0` are clamped to `1` and emit a warning in verbose mode (JPEG encoder rejects 0). | None. |
| BC6 | New opt-in flags: `--only`, `--skip`, `--roughness`, `--normal-format`, `--include-curvature`, `--seamless`, `--no-seamless`, `--jobs`, `--skip-existing`, `--progress`, inline `--height-contrast`/`--normal-strength`/etc, `--list-presets`, `--list-maps`, `--generate-completions`, `info` subcommand. | All additive. |
| BC7 | Curvature map is **opt-in** via `--include-curvature`; default output set stays at 6 maps. | Pass `--include-curvature` to enable the 7th map. |
| BC8 | New `materialize info <image>` subcommand (analyse without generating). | Additive. |
| BC9 | `MATERIALIZE_GPU_BACKEND` and `MATERIALIZE_LOG` env vars are finally honoured (were documented but not implemented in 1.0). | Set them as documented. |

### Added — Auto-detection (F2)

- **`-p auto`**: pre-pass CPU analysis (luminance, saturation, hue histogram,
  edge density, local contrast variance, tile MSE, alpha coverage) classifies
  the texture into one of the existing presets and prints the detected preset
  with a confidence score.
- **Auto-tile sampling**: when the analysis reports a tile MSE below 0.005, all
  neighbourhood-sampling shaders (height blur, normal Sobel, edge gradient, AO
  cavity, curvature Laplacian) switch from clamp to wrap (Euclidean modulo) at
  borders so the generated maps stay tileable. Manual override via
  `--seamless` / `--no-seamless`.
- **Auto-scale**: under `-p auto`, `height_contrast` and `normal_strength` are
  scaled by `edge_density` (detailed textures get more contrast; very noisy
  textures get softened normals).
- **Metallic local-variance damping (F2.5)**: pure metals have low local
  luminance variance while textured non-metals (concrete, gray stone) have
  high variance. A new uniform `metallic_local_variance_factor` (0..1) damps
  detection in textured regions, reducing false positives. Configurable via
  `--metallic-local-variance`.
- **`materialize info <image>`** subcommand: prints the analysis report
  (detected preset + confidence + all features) without generating any maps.

### Added — CLI/UX (F3)

- **Batch processing**: directory or glob input. `--jobs N` accepts a value
  for API stability (GPU dispatch is serialised; CPU load/analyse is the only
  parallelism in this release). `--skip-existing` resumes by skipping images
  whose height map already exists. `--progress` prints `[i/N]` per image.
- **Inline overrides**: `--height-contrast`, `--height-blur`, `--normal-strength`,
  `--normal-format <opengl|directx>`, `--metallic-scale`, `--metallic-local-variance`,
  `--smoothness-base`, `--smoothness-boost`, `--smoothness-roughness`,
  `--edge-contrast`, `--ao-depth-scale`. Each is `Option<f32>` applied on top
  of the selected preset.
- **Selective maps**: `--only height,normal` (whitelist) or `--skip edge,ao`
  (blacklist). Curvature stays opt-in via `--include-curvature`.
- **Roughness output**: `--roughness` exports `texture_roughness.png`
  (`1 - smoothness`) instead of `texture_smoothness.png`.
- **Normal flip-Y**: `--normal-format directx` flips the green channel for
  DirectX/Unity conventions (default remains OpenGL Y-up).
- **Curvature map**: `--include-curvature` adds `texture_curvature.png`
  (Laplacian of height: 0.5 = flat, >0.5 = concave, <0.5 = convex).
- **Verbose timing**: `-v` now prints per-stage millisecond timings
  (`height_ms`, `normal_ms`, …, `readback_ms`, `total_ms`).
- **Listing commands**: `--list-presets`, `--list-maps`.
- **Shell completions**: `--generate-completions <bash|zsh|fish|elvish|powershell>`.
- **12 new presets**: concrete, leather, marble, sand, foliage, plaster,
  asphalt, brick, ice, snow, lava, water.

### Added — Bug fixes (F1)

- **Edge map rewrite (F1.1, critical)**: the previous edge shader computed
  `(diff_x + 0.5) * (diff_y + 0.5) * 2.0` with gradients centred at 0, which
  produced an almost-flat ~0.5 output everywhere. Now uses the magnitude of
  the normal gradient with a smoothed threshold.
- **Granular exit codes (F1.2)**: see BC1.
- **Env vars (F1.3)**: `MATERIALIZE_GPU_BACKEND` (`vulkan|metal|dx12|gl|primary`)
  and `MATERIALIZE_LOG` (`error|warn|info|debug|trace`) are now read.
- **Shell completions (F1.4)**: see above.
- **Verbose timing (F1.8)**: see above.

### Changed — Quality of maps (F4)

- **Smoothness is now spatial (F4.1)**: combines `base + metallic_boost *
  metallic - roughness_factor * local_contrast_5x5`. Textured regions (high
  local contrast) produce lower smoothness; flat regions produce higher.
  Existing behaviour is preserved when `smoothness_roughness_factor == 0`.
- **Metallic detector redesigned (F4.5)**: replaced the overlapping hue bands
  (gold/copper subsets of each other) with two tiers: a single achromatic
  group (covers steel, silver, aluminum, titanium, pewter, chrome, blue steel)
  with an optional blue-tint bonus, plus four non-overlapping chromatic hue
  bands (copper / bronze / gold / brass).
- **Normal flip-Y** is now driven by a uniform (`normal_flip_y`) instead of
  being hard-coded.
- **`PresetParams`** grew three new fields: `normal_flip_y` (u32),
  `metallic_local_variance_factor` (f32), `smoothness_roughness_factor` (f32),
  and `seamless` (u32). Total size went from 48 → 64 bytes.

### Fixed

- Edge shader no longer produces a near-flat ~0.5 map (see F1.1).
- `MATERIALIZE_GPU_BACKEND` / `MATERIALIZE_LOG` documented-but-unimplemented
  since 1.0 are now honoured.
- `--generate-completions` documented-but-unimplemented since 1.0 is now
  honoured.
- Metallic detector no longer reports bronze/brass as subsets of copper/gold.

### Notes

- The `ao-mode` flag was deliberately **not** added (raymarched AO remains out
  of scope for 2.0; cavity AO from 1.0 is still the only implementation).
- The poll pattern in `gpu.rs::read_texture` is intentional and required for
  native wgpu (`map_async` only resolves once the device is polled); a
  previous draft of this plan proposed removing it, which would deadlock.
- `F1.7` (poll fix) and `F1.6` (NaN guard) from the plan were dropped: the
  poll is not a bug, and the NaN division in `metallic.wgsl` is unreachable
  because `delta > 0` is guarded before the denominator is computed. A
  defensive `max(1e-6, …)` was added anyway.

## [1.0.0] - 2026-03-15

### Added

- Height map generation from diffuse (multi-level box blur + contrast).
- Normal map generation from height (Sobel operator).
- Metallic map generation from diffuse (HSL-based detection).
- Smoothness map (`base + metallic_boost * metallic`).
- Edge map from normal gradient.
- AO map (cavity-style from height).
- CLI interface with clap (input, output dir, format, quality, verbose, quiet).
- Support for PNG, JPG, TGA, EXR.
- GPU processing via wgpu compute shaders.
- Integration tests and unit tests.
- Seven material presets: default, skin, floor, metal, fabric, wood, stone.
