# Part3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

Decomposição semântica de meshes 3D via **Hunyuan3D-Part** (P3-SAM + X-Part): segmentação e geração de partes. Alinhado aos padrões Shared (QualityEngine, SDNQ, hw-auto, `ensure_vram` / exclusive GPU, UMS).

## Requisitos

- Python **3.13**
- GPU NVIDIA com CUDA (~6 GB VRAM com offload + SDNQ)
- Instalação: `./install.sh part3d` (registo em `tools.yaml`)

## Instalação

```bash
./install.sh part3d
# ou:
cd Shared && pip install -e . && cd ../Part3D && pip install -e .
```

## Uso

```bash
part3d --help
part3d decompose mesh.glb -o partes.glb -v
part3d decompose mesh.glb --quality fast
part3d decompose mesh.glb -q int4          # SDNQ int4
part3d decompose mesh.glb --gpu-ids 0,1    # DiT / auxiliares
```

Hw-auto (default): GPUs &lt;8 GiB → memory-efficient (SDNQ uint8 + CPU offload). Kill-switch: `PART3D_HW_AUTO=0`.

Documentação completa: [README.md](README.md).
