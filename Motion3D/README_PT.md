# Motion3D

CLI text-to-motion do AiGameKit com **Tencent HY-Motion-1.0** (Lite por omissão / Full via hw-auto ou `--model full`).

Pipeline: prompt → vramd `motion3d` → encoders HY (CLIP+Qwen) → HunyuanMotionMMDiT → WoodenMesh FK → NPZ `joints (T,22,3) @ 30fps` → `apply-rigged` (Animator3D `hml22`) → GLB skinned.

## Instalar

```bash
./install.sh motion3d
motion3d doctor
```

## Happy path

```bash
motion3d generate "a person walks forward" -o walk.npz --quality medium
motion3d apply-rigged walk.npz hero_rigged.glb -o hero_walk.glb \
  --clip walk --in-place
```

Após editar código: `vramd respawn motion3d`.

## VRAM

Hw-auto no estilo Text2D (`plan_offload`): em **~6 GB** escolhe **Full** com text encoder em CPU (staged), pode aplicar SDNQ no DiT e reduzir `validation_steps` / duração. Rewriter LLM **desligado**.

Pesos: [`tencent/HY-Motion-1.0`](https://huggingface.co/tencent/HY-Motion-1.0) → `~/.cache/aigamekit/models/hy-motion-1.0/`.

Licença: [THIRD_PARTY.md](THIRD_PARTY.md).
