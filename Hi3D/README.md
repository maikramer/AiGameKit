# Hi3D — geometry refinement for the 3D pipeline

Image-conditioned **mesh refinement** built on
[Hi3D v1.0 / SPARC3D](https://huggingface.co/hi3d-official/Hi3Dv1.0). Takes a
coarse mesh + the concept image that produced it and returns a denser, more
faithful surface. In AiGameKit it runs as **post-processing of the shape stage**
(`topology-fix` → **`refine`** → `paint`), enabled by default.

```bash
hi3d refine clean.glb -i reference.png -o refined.glb          # 512³, phase-staged (60 s cold)
hi3d refine clean.glb -i reference.png -o refined.glb --target-faces 60000
hi3d refine clean.glb -i reference.png -o refined.glb --no-staging   # upstream offload (137 s, 0.9 GiB)
hi3d doctor                                                     # stack + GPU + weights + plan
hi3d download-model                                             # 8 artifacts, ~7.7 GB
```

Inside the toolkit you normally don't call it directly — `gameassets batch`
does it through `text3d refine`.

## Install

```bash
./install.sh hi3d        # venv 3.10 + torch cu124 + spconv + flash-attn + sparc3d
hi3d download-model      # ~7.7 GB of weights into ~/.cache/aigamekit/models/hi3d-v1.0
hi3d doctor
```

The installer is non-interactive and prints the license note. Without the
weights everything still works: `refine` reports "unavailable" (exit 3) and the
batch keeps going with the `_clean` mesh.

## Why its own Python 3.10 venv

The upstream only measured one stack: Python 3.10.12, torch 2.5.0+cu124, CUDA
12.4, `flash-attn==2.7.4.post1`, `spconv-cu120==2.3.6`, `transformers==4.46.3`.
`pyproject.toml` here pins `>=3.10,<3.11` and declares **no runtime
dependencies** on purpose — the custom installer hook
(`aigamekit_shared.installer.hi3d_extras`) installs the measured stack so `uv`
never re-resolves `torch` to a CPU/other-CUDA build. It's the only 3.10 venv in
the monorepo; every other tool stays on 3.13.

## Measured on a 6 GB laptop GPU (RTX 4050, CC 8.9)

| Setting | Wall time | Peak VRAM | Output |
|---|---|---|---|
| **Phase-staged** (default), vramd worker warm | **32 s** | 3.74 GiB | 32k faces (decimated) |
| Phase-staged, fresh process (load included) | **60 s** | 3.74 GiB | 543k faces → decimated |
| Upstream sequential offload (`--no-staging`) | 137 s | 0.86 GiB | same |
| Phase-staged, 5 steps | 32 s | 3.74 GiB | — |

The upstream only offers "everything on the GPU" (7.7 GB — too big for 6 GB) or
`enable_sequential_cpu_offload`, which moves **every submodule** on every
forward: 25 steps cost ~92 s of PCIe traffic with the GPU almost idle (238 MiB
peak). Phase staging moves one whole component (conditioner, transformer, VAE)
onto the GPU at the start of its phase and back at the end — 92 s → 27 s.

Measured and **rejected** (don't retry without a new bench): fp16 autocast (no
gain — the model is already mixed fp16/fp32), fp16 weights (`polar_cuda` RoPE
crash), `torch.compile` (cold ~50 s, no hot gain; `reduce-overhead` crashes on
CUDA graphs), `assign=True`/meta-device loading (the 19.7 s is model
*construction*, and meta leaves 24 buffers without data).
`--fast-postprocess` (skip the upstream component filter) saves ~1 s and is
opt-in. Full numbers: [`docs/findings/HI3D_REFINE_FINDINGS.md`](../docs/findings/HI3D_REFINE_FINDINGS.md).

The model card publishes no minimum VRAM (1536³ was validated on a 96 GB H20).
The thresholds in `capability.py` are conservative heuristics for this monorepo:

* compute capability ≥ 8.0 (Ampere) — hard requirement (flash-attn/spconv, no CPU path);
* 512³ (1 pass) needs ≥ 4.8 GiB free;
* 1024³ (2 passes) from 16 GiB free; 1536³ (3 passes) from 40 GiB free;
* phase staging (one component resident at a time) from 4.6 GiB free, which is
  what makes 512³ fit in 6 GB; below that it falls back to the upstream
  sequential offload (0.86 GiB peak, 2.3x slower).

`hi3d doctor --json` prints the plan for the current machine; `vramd calibrate hi3d`
replaces the estimates with real measurements.

## What it does to the mesh

The upstream returns geometry **centered on the vertex mean with unit maximum
radius** — it does not preserve world coordinates. `refine` therefore:

1. records the coarse mesh frame (`center = vertices.mean()`, `radius = max‖v−center‖`);
2. runs the refinement ladder;
3. inverts the normalization verbatim (`v * radius + center`), so scale/origin
   match the `_clean` mesh that feeds paint (feet at y=0, meters from `size_m`);
4. decimates with quadric decimation to `--target-faces` (0 = keep ~1M);
5. strips islands below 0.5 % of the largest component (the same ratio the
   upstream uses before simplification — decimation shreds tiny shards, e.g.
   214 islands of 1–7 faces on a 32k hero);
6. exports a geometry-only GLB (no UVs/materials — paint comes next).

The result is a closed surface: on the 6 GB test asset, 127,986 faces, **1
connected component, 0 boundary edges, 0 non-manifold edges**. `text3d refine`
does **not** run `topology_clean` by default (`--clean` opts in): measured, the
clean opens the mesh (2,804 boundary edges) and shreds 298 micro-islands while
losing 15 % of the faces — a regression over the marching-cubes surface. The
only thing it used to add (weld) is already handled by the upstream sampler.

## vramd integration

`hi3d` is a registered backend (`Shared/src/aigamekit_shared/data/backends.yaml`,
`vram_mib: 4300` — the phase-staged peak). The worker speaks the canonical JSONL protocol
(`serve --ums-worker`) implemented locally in `hi3d/ums.py`: the Hi3D venv is
3.10 and `aigamekit_shared` requires 3.13, so the package cannot import it.
`tests/test_ums.py` parses `Shared/.../worker_protocol.py` and asserts every
command/event/error constant still matches — drift is a test failure.

## Pipeline configuration (`game.yaml`)

```yaml
refine:
  enabled: true          # default ON
  resolution: auto       # 512 | 1024 | 1536 | auto (VRAM policy)
  steps: 25
  max_faces_scale: 4.0   # refined cap = 4 × LOD0 target faces (0 = no cap)
  seed: 0
  remove_background: false
```

The stage is skipped (with a visible reason) when the binary, weights, a
suitable GPU, or the reference image are missing. It never fails an asset.
Disable globally with `refine: false` or `refine: {enabled: false}`.

### Multiview

Hi3D v1.0 itself accepts **one image per pass** (`--image` is scalar and
`ImageConditioner` rejects lists). The GameAssets pipeline can approximate
multiview by generating one Text2D image per view and chaining one `hi3d refine`
pass per view (`refine.views`). See
[GameAssets/README.md](../GameAssets/README.md#hi3d-multiview-refine-optional).

## CLI

```
hi3d refine COARSE -i IMAGE -o OUT [--resolution auto|512|1024|1536] [--steps N]
                   [--seed N] [--target-faces N] [--align/--no-align]
                   [--model DIR|REPO] [--offload/--no-offload]
                   [--staging/--no-staging] [--fast-postprocess]
                   [--remove-background] [--local-files-only]
                   [--dino-model DIR] [--clip-model DIR] [--json] [-v]
hi3d doctor [--json]
hi3d download-model [--model REPO]
hi3d serve --ums-worker
```

Exit codes: `0` ok, `1` error, `3` unavailable on this hardware/installation —
the orchestrator uses 3 to skip gracefully.

## License

The Hi3D second-stage implementation and the eight model artifacts are under the
**Hi3D Open License Agreement 1.0**. Commercial use is permitted under these
conditions (summary, not legal advice — read `LICENSE`/`NOTICE` in the model
repo):

* commercial integrations must display **"Powered by Hi3D 1.0"**, linking to the
  official site where the medium supports it;
* above **200,000 monthly active users or USD 1,000,000 monthly revenue** you
  must notify the licensor and apply for a separate written license within 60
  days;
* **standalone outputs** that contain no Hi3D materials or model derivatives
  (the refined GLB on its own) are not subject to the branding/clause by
  themselves — other law and third-party rights still apply;
* DINOv2 (`facebook/dinov2-large`) and CLIP (`openai/clip-vit-large-patch14`)
  conditioning encoders keep their own licenses;
* the optional Hunyuan3D coarse example upstream is a separate dependency.

## Limitations

* Geometry only — no textures, materials or vertex colors.
* Batch size one; no progress events during decode/extract.
* Severe structural errors in the coarse mesh can persist; hidden geometry is a
  plausible completion, not a reconstruction.
* Seeds are not mesh hashes (GPU sparse conv/attention are not bitwise
  deterministic).
* The upstream ships inference only; no training/eval code.
