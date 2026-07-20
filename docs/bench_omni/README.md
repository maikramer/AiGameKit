# Bench Omni — Quaternius T-pose control

Skeleton control for Hunyuan3D-Omni pose conditioning.

**Descobertas / knobs / armadilhas (bbox max, presets, decode, falhas de
batch):** ver [`docs/OMNI_SHAPE_FINDINGS.md`](../OMNI_SHAPE_FINDINGS.md).

**Hub multi-modelo (VRAM / kernels / UMS):** [`docs/MODEL_FINDINGS.md`](../MODEL_FINDINGS.md).

**Packaged (preferred):** `text3d --pose-preset quaternius-tpose` uses
`Text3D/src/text3d/data/omni/` (bone.txt + reference GLB).

| File | Description |
|------|-------------|
| `quaternius_tpose_bone.txt` | Omni pose format: 51 lines × `(hx hy hz tx ty tz)` |
| `quaternius_tpose_bone.json` | Same bones with Quaternius names + metadata |

Source: Quaternius UAL `UAL1_Standard.glb` action `A_TPose` (CC0).
Coords: remapped Z-up→Y-up, centered, scaled to ~[-0.99, 0.99].

```bash
text3d generate -i path/to/hero.png \
  --pose-preset quaternius-tpose \
  -o docs/bench_omni/hero_omni_tpose_shape.glb \
  --no-topology-fix --sdnq-preset sdnq-int4 --volume-decoder flashvdm
```

Smoke (RTX 4050 6GB, 2026-07-17): `hero_omni_tpose_shape.glb` ~15.6 MB, ~1060s,
Shape VRAM ~3.1 GB after SDNQ int4 + `place_pipeline` (requires Omni `.to()`).
