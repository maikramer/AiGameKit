# Rigging3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI de **auto-rigging 3D** baseado no [UniRig](https://github.com/VAST-AI-Research/UniRig) (MIT).

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh rigging3d
```

Este comando **instala sempre** a stack de inferência completa (PyTorch CUDA, `bpy`, Open3D, spconv, PyG, etc.) — mesmo comportamento que `gamedev_shared.installer.unified`. Guia: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento (`scripts/setup.sh`)

Um único comando no directório do projecto: venv, PyTorch+CUDA, dependências de inferência, spconv e torch-scatter/cluster.

```bash
cd Rigging3D
bash scripts/setup.sh
```

O script auto-detecta a versão CUDA do driver. Requer **Python 3.11** (wheels `bpy==5.0.1` e **Open3D** no PyPI; ver nota sobre Blender 5.1 abaixo).

```bash
bash scripts/setup.sh --python python3.11    # especificar interpretador
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
cd Rigging3D && python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[inference]"
```

**Windows:** o fluxo de inferência completo foi testado em **Linux**; no Windows usa `python scripts/installer.py --inference` (Python por defeito `python` se `PYTHON_CMD` não estiver definido).

**Se o PyTorch ficar só em CPU** (p.ex. `nvidia-smi` sem linha «CUDA Version» por NVML/driver): define `RIGGING3D_FORCE_CUDA=1` e volta a correr o instalador com `--inference`, ou usa `bash scripts/setup.sh` que aplica a mesma lógica. Opcional: `RIGGING3D_PYTORCH_CUDA_INDEX` para outro índice de wheels CUDA.

### Deps CUDA-specific (se instalou manualmente)

O `setup.sh` instala tudo automaticamente, mas se precisares instalar manualmente, usa a mesma URL PyG que o script (depende da versão de `torch` e do CUDA runtime). Com **Python 3.11**, confirma que existe wheel `torch-*+cu*` para a tua combinação; caso contrário o `setup.sh` tenta compilação a partir do source.

```bash
# torch-scatter + torch-cluster (ajustar torch e CUDA ao teu venv):
pip install torch-scatter torch-cluster -f https://data.pyg.org/whl/torch-2.11.0+cu130.html

# spconv + cumm (cu121 para CUDA 12.x e 13.x):
pip install cumm-cu121 spconv-cu121
```

### Pesos do modelo

Os pesos HF são descarregados automaticamente na 1.ª execução: [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig). Confirma termos no card (ver [GameDev/README_PT](../README_PT.md)).

## Requisitos

- Python **3.11** (intervalo suportado pelo `pyproject.toml` da inferência; `bpy` 5.0.1 no PyPI)
- GPU NVIDIA com CUDA (≥6–8 GB VRAM conforme mesh; GPUs mais pequenas podem falhar em meshes muito densos)
- **bash** para scripts de inferência — no Windows: Git Bash ou MSYS2

### Blender 5.1.0, `bpy` e Open3D

- No **PyPI**, o wheel **`bpy==5.1.0`** (alinhado ao Blender **5.1.0**) só existe para **Python 3.13**.
- O pacote **Open3D** usado pelo UniRig **não** publica wheels estáveis para **Python 3.13** (apenas até `cp312` na versão actual).
- Por isso o Rigging3D mantém **`bpy==5.0.1`** em **Python 3.11** para inferência completa (mesh + merge com Open3D). A API é da linha **Blender 5.0**, próxima da 5.1 para a maior parte dos operadores `bpy.ops` usados no pipeline.
- Para **`bpy==5.1.0`** igual ao teu Blender 5.1.0, usa o projecto [**Animator3D**](../Animator3D/) com **Python 3.13** (só animação/export, sem Open3D no mesmo venv).

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

## Licença

- Rigging3D (CLI): **MIT** — [`LICENSE`](LICENSE)
- Código UniRig: **MIT** — [`unirig/LICENSE`](src/rigging3d/unirig/LICENSE) · [`THIRD_PARTY.md`](THIRD_PARTY.md)
- **Pesos HF:** o repositório [VAST-AI/UniRig](https://huggingface.co/VAST-AI/UniRig) pode não incluir `LICENSE` na raiz; valida termos no card e em forks com ficheiro explícito se necessário. Tabela no [README do monorepo](../README_PT.md).
