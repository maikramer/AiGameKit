# Part3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

Decomposição semântica de meshes 3D via **Hunyuan3D-Part** (P3-SAM + X-Part): segmentação e geração de partes.

## Requisitos

- Python **3.10+**
- GPU NVIDIA com CUDA recomendada (~5 GB VRAM pico com offloading; ver CLI)
- Registo da ferramenta: [`Shared/src/gamedev_shared/installer/registry.py`](../Shared/src/gamedev_shared/installer/registry.py)

## Instalação

### Oficial (monorepo)

Na raiz do repositório **GameDev**:

```bash
cd /caminho/para/GameDev
./install.sh part3d
```

Equivalente: `gamedev-install part3d` (com `gamedev-shared` instalado ou `PYTHONPATH=Shared/src`).

### Manual / avançado

```bash
cd Part3D
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Nota: o instalador oficial adiciona **torch-scatter** e **torch-cluster** após o PyTorch (ver `gamedev_shared.installer.part3d_extras`).

### Atalho local

```bash
cd Part3D
python3 scripts/installer.py
```

## Uso

```bash
part3d --help
part3d decompose mesh.glb -o partes.glb -v

# Multi-GPU: distribuir o DiT por várias GPUs (só afecta a etapa DiT)
part3d decompose input.glb output/ --gpu-ids 0,1
```

Documentação geral de instalação: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)
