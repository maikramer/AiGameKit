# Rigging3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI de **auto-rigging 3D** baseado no [SkinTokens](https://github.com/VAST-AI-Research/SkinTokens) / TokenRig (MIT) — modelo autoregressivo unificado (skeleton + skinning), sucessor do UniRig.

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório AiGameKit:

```bash
cd /caminho/para/AiGameKit
./install.sh rigging3d
```

Este comando **instala sempre** a stack de inferência completa (PyTorch CUDA, `bpy`, Open3D, spconv, PyG, etc.) — mesmo comportamento que `aigamekit_shared.installer.unified`. Guia: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento (`scripts/setup.sh`)

Um único comando no directório do projecto: venv, PyTorch+CUDA, dependências de inferência, spconv e torch-scatter/cluster.

```bash
cd Rigging3D
bash scripts/setup.sh
```

O script auto-detecta a versão CUDA do driver. Requer **Python 3.13** (`bpy>=5.2.0` LTS no PyPI — só existem wheels `cp313`).

```bash
bash scripts/setup.sh --python python3.13    # especificar interpretador
bash scripts/setup.sh --force                # recriar venv do zero
```

**Atenção:** o pipeline usa `torch.nn.functional.scaled_dot_product_attention` (SDPA) do PyTorch — não é necessário o pacote `flash-attn`.

### Atalho local (`scripts/installer.py`)

- **`./install.sh rigging3d`** (na raiz) equivale a **`python3 scripts/installer.py --inference`** nesta pasta (inferência completa).
- **Sem** `--inference`: só `pip install -e` + wrappers; o sumário indica como completar (útil para CI mínimo).

```bash
cd Rigging3D
python3 scripts/installer.py --inference
```

### Manual (passo a passo)

```bash
cd Rigging3D && python3.13 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

**Windows:** o fluxo de inferência completo foi testado em **Linux**; no Windows usa `python scripts/installer.py --inference` (Python por defeito `python` se `PYTHON_CMD` não estiver definido).

**Se o PyTorch ficar só em CPU** (p.ex. `nvidia-smi` sem linha «CUDA Version» por NVML/driver): define `RIGGING3D_FORCE_CUDA=1` e volta a correr o instalador com `--inference`, ou usa `bash scripts/setup.sh` que aplica a mesma lógica. Opcional: `RIGGING3D_PYTORCH_CUDA_INDEX` para outro índice de wheels CUDA.

### Deps CUDA-specific (se instalou manualmente)

O `setup.sh` instala tudo automaticamente, mas se precisares de instalar manualmente torch-scatter/torch-cluster, usa a mesma URL PyG que o script (depende da versão de `torch` e do CUDA runtime). Com **Python 3.13**, confirma que existe wheel `torch-*+cu*` para a tua combinação; caso contrário o `setup.sh` tenta compilação a partir do source.

```bash
# torch-scatter + torch-cluster (ajustar torch e CUDA ao teu venv):
pip install torch-scatter torch-cluster -f https://data.pyg.org/whl/torch-2.11.0+cu130.html

# spconv + cumm (cu121 para CUDA 12.x e 13.x):
pip install cumm-cu121 spconv-cu121
```

### Pesos do modelo

Os pesos HF são descarregados automaticamente na 1.ª execução: [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) (~1,6 GB; MIT). Ver [AiGameKit/README_PT](../README_PT.md).

## Requisitos

- Python **3.13** (`>=3.13,<3.14`; `bpy>=5.2.0` LTS no PyPI — só wheels `cp313`)
- GPU NVIDIA com CUDA (~3.9 GB VRAM medidos num asset denso no RTX 4050 6 GB com defaults; GPUs mais pequenas podem falhar em meshes muito densos)
- **bash** para scripts de inferência — no Windows: Git Bash ou MSYS2

### `bpy>=5.2.0` e SkinTokens

- No **PyPI**, o wheel **`bpy>=5.2.0`** (Blender **5.2 LTS**) só existe para **Python 3.13** — daí o Rigging3D exigir essa combinação.
- O **SkinTokens** (sucessor do UniRig) **não exige Open3D nem `flash-attn`** — corre em SDPA nativo do PyTorch (`torch.nn.functional.scaled_dot_product_attention`) durante todo o pipeline.

## Uso

```bash
rigging3d pipeline --input mesh.glb --output rigged.glb
rigging3d skeleton --input mesh.glb --output skel.glb
rigging3d skin    --input skel.glb --output skin.glb
rigging3d merge   --source skin.glb --target mesh.glb --output rigged.glb

# Multi-GPU: propagar CUDA_VISIBLE_DEVICES para subprocessos (skeleton, skin, merge)
rigging3d --gpu-ids 0,1 pipeline --input mesh.glb --output rigged.glb
```

Para apontar a outra árvore de inferência:

```bash
export RIGGING3D_ROOT=/outro/caminho
```

## Comandos

| Comando | Descrição |
|---------|-----------|
| `skeleton` | Gera skeleton (GLB; `.fbx` ainda suportado) |
| `skin` | Skinning weights |
| `merge` | Junta skin + mesh original |
| `pipeline` | skeleton → skin → merge |

## Unified Model Server (UMS)

`rigging3d pipeline` delega automaticamente no **`aigamekit-model-server`** — o supervisor GPU do monorepo (um processo, um socket, fila com prioridade + afinidade VRAM, evicção peso + LRU, workers subprocess por tool). Auto-arranca no primeiro uso salvo `AIGAMEKIT_UMS_AUTO_START=0`.

```bash
rigging3d pipeline char.glb -o char_rigged.glb --ums-stream
rigging3d pipeline char.glb -o char_rigged.glb --no-ums   # forçar in-process
ums status
ums respawn rigging3d                                     # recarrega código src/ editado
```

Modelo: [SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) (TokenRig; download público, MIT — ~1,6 GB descarregados automaticamente no primeiro run). Guia completo: [`ModelServer/README.md`](../ModelServer/README.md).

## Licença

- Rigging3D (CLI): **MIT** — [`LICENSE`](LICENSE)
- Código SkinTokens: **MIT** — [`THIRD_PARTY.md`](THIRD_PARTY.md) (sucessor do UniRig; o código `unirig/` foi removido na migração)
- **Pesos HF:** [VAST-AI/SkinTokens](https://huggingface.co/VAST-AI/SkinTokens) — descarregados automaticamente (~1,6 GB). Tabela no [README do monorepo](../README_PT.md).
