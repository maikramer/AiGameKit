# Motion3D

Text-to-motion CLI for AiGameKit using **Tencent HY-Motion-1.0** (Lite default / Full via hw-auto or `--model full`).

Pipeline: prompt → UMS `motion3d` → HY text encoders (CLIP+Qwen) → HunyuanMotionMMDiT → WoodenMesh FK → NPZ `joints (T,22,3) @ 30fps` → optional `apply-rigged` (Animator3D `hml22`) → skinned GLB.

## Install

```bash
./install.sh motion3d   # Motion3D/.venv + cross_deps animator3d
motion3d doctor
```

## Happy path

```bash
# Generate (GPU via UMS by default; hw-auto picks Full on ~6GB with staged text-CPU)
motion3d generate "a person walks forward" -o walk.npz --quality medium

# Apply onto SkinTokens *_rigged.glb (in-place, loopable)
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb \
  --clip walk --in-place

# Pack several NPZs onto one skinned GLB (walk/run/jump/sprint, …)
motion3d pack-rigged hero_rigged.glb -o hero_locomotion.glb --active walk \
  -m walk=walk.npz -m run=run.npz -m jump=jump.npz -m sprint=sprint.npz
```

Each `-m name=path.npz` becomes a named glTF animation on the same mesh/skin
(`export_animation_mode=ACTIONS`, same contract as Animator3D `game-pack`).
`--active` picks the default action for viewers that ignore extra clips.

### Movement constraints

HY-Motion ignores contact and posture wording in the prompt: hands drift apart,
feet shuffle, and a "swing down" folds the spine ~60°. `apply-rigged` takes
mechanical constraints you can state per clip — off by default, named after body
mechanics rather than any one gesture:

| Flag | Effect |
|------|--------|
| `--hands-together 0.10` | Both wrists within N meters, same height, uncrossed — any two-hand prop (axe, staff, greatsword) rides the midpoint |
| `--plant-feet` | Stance rigid under the pelvis — stationary actions stop shuffling |
| `--max-lean 25` | Cap torso tilt off vertical, arms carried along — a swing stays a swing instead of a bend-over |

```bash
# Two-handed axe chop at waist height, one shot
motion3d generate "a person chops down at a tree trunk with a two-handed axe, \
swinging from above the shoulder down to waist height, then lifts the axe back up" \
  -o chop.npz --duration 4
motion3d apply-rigged chop.npz hero_rigged.glb -o hero_chop.glb \
  --clip chop --max-lean 25 --hands-together 0.10 --plant-feet
```

After editing Motion3D code: `ums respawn motion3d`.

## Flags

| Flag | Role |
|------|------|
| `--duration` | Seconds @ 30fps (QualityEngine soft-fill) |
| `--frames` | Alternate length cap (`duration = frames/30`) |
| `--model lite\|full` | Explicit variant (else hw-auto / quality) |
| `--cfg-scale` | CFG guidance |
| `--sdnq-preset` | DiT quant (`none` / `sdnq-uint8` / `sdnq-int4`) |
| `--quality` | Soft defaults for duration / cfg / steps / model |
| `--no-ums` | In-process fallback |

## VRAM (Text2D-style hw-auto)

- Planner: `aigamekit_shared.lowvram.plan_offload` + staged load (DiT on GPU, Qwen encode on CPU when tight).
- On **~6 GB**: prefers **Full** + text-CPU + optional SDNQ; clamps `validation_steps` / duration.
- Prompt-engineering LLM rewriter is **off** (saves VRAM).
- UMS footprint keys: `hy-motion-lite` / `hy-motion-full` (DiT-resident, not stacked Qwen).

## Weights

HF: [`tencent/HY-Motion-1.0`](https://huggingface.co/tencent/HY-Motion-1.0)

Cache: `~/.cache/aigamekit/models/hy-motion-1.0/`

## License

HY-Motion Community License — see [THIRD_PARTY.md](THIRD_PARTY.md).
