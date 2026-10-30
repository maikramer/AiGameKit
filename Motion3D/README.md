# Motion3D

Text-to-motion CLI for AiGameKit running [**SprongMotion-14M**](https://huggingface.co/SegerEnd/SprongMotion-14M)
on the CPU: one 33 MB GGUF file plus a vendored C11 runtime. No PyTorch, no GPU,
no VRAM planning.

Pipeline: prompt → SprongMotion (`22 joints @ 20 fps`) → NPZ
(`joints`, `rotations`, `offsets`, `root`, `contacts`, codes) → optional
`apply-rigged` (Animator3D retarget, profile `sprong22`) → skinned GLB.

## Install

```bash
./install.sh motion3d     # part of the default `core` profile
motion3d doctor           # compiler, runtime, model (sha256), bpy, retarget profile
motion3d download-model   # ~33 MB from HuggingFace (also runs on first generate)
```

The runtime is compiled from `src/motion3d/vendor/sprongmotion/` into
`~/.cache/aigamekit/sprongmotion/<version>-<source-hash>/` on first use. Needs a
C11 compiler (`cc`; on Debian/Ubuntu `build-essential`). Overrides:
`SPRONG_CC`, `SPRONG_CFLAGS`, `SPRONG_LIB` (prebuilt library), `SPRONG_MODEL`
(local `.gguf`).

## Happy path

```bash
# Generate (CPU; ~0.2 s per 2 s clip on a laptop)
motion3d generate "a person walks forward" -o walk.npz --duration 2

# Apply onto a SkinTokens *_rigged.glb (in-place, loopable)
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb --clip walk

# Pack a locomotion set onto one skinned GLB (ACTIONS export)
motion3d pack-rigged hero_rigged.glb -o hero_locomotion.glb --active walk \
  -m walk=walk.npz -m run=run.npz -m jump=jump.npz -m idle=idle.npz
```

Each `-m name=path.npz` becomes a named glTF animation on the same mesh/skin
(same contract as Animator3D `game-pack`); `--active` picks the default action
for viewers that ignore extra clips.

`-o file.glb` writes the intermediate source armature instead of an NPZ
(useful to inspect the motion in Blender); `--bvh other.bvh` also writes the
runtime's own BVH.

### Seeds and `--attempts`

The model card is explicit: results vary a lot between seeds, and a good share
of clips need another seed before they are usable. Generation is cheap, so the
CLI generates **3 seeds by default** (`--attempts N`) and keeps the one with the
lowest mechanical score (planted-foot slide, ground penetration, root jerk —
see `motion3d/quality.py`). `--attempts 1` pins a single seed; the chosen seed
is recorded in the NPZ, so a run is reproducible from its file.

## Flags

| Flag | Role |
|------|------|
| `--duration/--seconds` | Clip length (0.5–10 s; engine is 20 fps) |
| `--frames` | Alternative to duration (`seconds = frames / 20`) |
| `--seed` | RNG seed; same seed + same build = identical motion |
| `--attempts` | Seeds to try, keep the cleanest (default 3) |
| `--steps` | Base-layer unmasking steps (1–64, default 10) |
| `--residual-layers` | Residual layers (0–5; 0 = fast draft) |
| `--text-guidance` / `--residual-guidance` | Classifier-free guidance (0–20) |
| `--temperature`, `--top-k` | Sampling controls |
| `--loop` | Seamless cycle (walk/run/idle) |
| `--foot-lock/--no-foot-lock` | Planted feet stay put (default on) |
| `--in-place` | Strip horizontal travel in the generated clip itself |
| `--quality` | Soft defaults for duration/steps/residual layers/attempts |
| `--threads` | Runtime threads (0 = all cores) |
| `--bvh`, `--also-npz`, `--json` | Extra artifacts / machine-readable result |

`apply-rigged`/`pack-rigged` keep the mechanical constraints from the previous
pipeline for game clips — all opt-in:

| Flag | Effect |
|------|--------|
| `--in-place` (default on) | Loopable clip: horizontal travel and yaw drift removed |
| `--max-lean 25` | Cap torso tilt off vertical (degrees) |
| `--hands-together 0.10` | Both wrists within N metres (two-hand prop) |
| `--plant-feet` | Freeze the stance horizontally (stationary actions) |
| `--arm-neutral auto\|on\|off` | Soft hang calibration; `auto` skips raised-arm clips |

## Prompting

One action per prompt, plain English: *"a person walks forward"*, *"waves with
the right hand"*, *"jumps up high"*. Give runs a direction (*runs forward*, not
*runs*). Style, side, speed and repeat counts are part of the training
captions. Sitting/lying use an invisible prop (game-animation convention).

## Limitations (from the model card)

- Flips and somersaults can break down; fast spins can glitch for single frames.
- Seams between blocks with different prompts are rougher than inside a block.
- Feet can slide in walks, most when switching walk → stand.
- 22 joints, no fingers; no object contact.
- English only.

## Weights, runtime and licenses

- Model: [`SegerEnd/SprongMotion-14M`](https://huggingface.co/SegerEnd/SprongMotion-14M), Apache-2.0,
  `SprongMotion-14M-v1.0-Q8_0.gguf` (SHA-256 pinned in `motion3d/weights.py`).
- Runtime: [sprongmotion-runtime](https://github.com/SegerEnd/sprongmotion-runtime) v1.1.0, vendored in
  `src/motion3d/vendor/sprongmotion/` (see `VENDORED.md` and [THIRD_PARTY.md](THIRD_PARTY.md)).

After editing Motion3D code there is no vramd worker to respawn — the CLI runs
in-process.
