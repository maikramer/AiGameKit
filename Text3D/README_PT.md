# Text3D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

**Text-to-3D** em duas fases: **[Text2D](../Text2D)** (texto → imagem) e **[Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1)** (imagem → mesh, SDNQ INT4 quantizado). O modelo 2D é **sempre descarregado** antes de carregar o Hunyuan3D.

Os **valores por defeito** do CLI/API estão em [`src/text3d/defaults.py`](src/text3d/defaults.py): perfil **~6 GB VRAM** (CUDA) **validado na prática** (boa qualidade text-to-3D com os mesmos números que o comando sem flags extra). O **Text2D (FLUX)** usa **CPU offload** por defeito (`DEFAULT_T2D_CPU_OFFLOAD`), senão o modelo não cabe na GPU. Em GPU grande, `--t2d-full-gpu`. `--low-vram` força o **Hunyuan** em CPU (último recurso).

**Atalhos:** `--preset fast` (menos tempo/VRAM), `balanced` (igual aos defeitos), `hq` (alta qualidade, GPU grande) — ajusta `--steps`, `--octree-resolution` e `--num-chunks` em conjunto (se usares `--preset`, não esperes que `--steps`/`--octree-resolution`/`--num-chunks` “ganhem” ao perfil — o preset tem prioridade). **`text3d doctor`** verifica PyTorch e VRAM. O CLI define `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` se a variável ainda não existir (menos fragmentação de VRAM).

**Textura e PBR** não fazem parte deste pacote: usa **[Paint3D](../Paint3D)** (`paint3d texture` — GLB PBR com Hunyuan3D-Paint 2.1) ou **[GameAssets](../GameAssets)** com `text3d.texture` no perfil.

> **Licença dos pesos Hunyuan:** [Tencent Hunyuan Community License](https://huggingface.co/tencent/Hunyuan3D-2.1) — lê o ficheiro `LICENSE` no repositório ([Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1)): restrições de território, política de uso aceitável e obrigações. **Text2D (FLUX):** o default SDNQ no monorepo não é o mesmo regime que o BF16 Apache 2.0 da BFL — ver [Text2D/README](../Text2D/README.md) e [GameDev/README_PT](../README_PT.md).

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## Requisitos

| Configuração | Mínimo | Recomendado |
|-------------|--------|-------------|
| Python | 3.10+ | 3.11+ |
| GPU | Opcional | CUDA ~6 GB+ (defeitos já calibrados) |
| RAM | 16GB | 32GB |
| Disco | ~20GB livres | Mais (cache Hugging Face) |

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh text3d
```

Instala o pacote em modo editável no `Text3D/.venv`, config em `~/.config/text3d`, wrappers em `~/.local/bin` (todas as plataformas; `%USERPROFILE%\.local\bin` no Windows). Variável opcional: `PYTHON_CMD`. Opção CLI: `--skip-env-config` (não escrever `env.sh` / `env.bat`). Textura: instala **[Paint3D](../Paint3D)** à parte.

Equivalente: `gamedev-install text3d`. Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / avançado

O [`config/requirements.txt`](config/requirements.txt) referencia `text2d @ file:../Text2D`. O código de geração de shape `hy3dshape` do [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1) está vendorizado em `src/text3d/hy3dshape/`.

```bash
cd GameDev/Text3D
python -m venv .venv && source .venv/bin/activate
pip install -r config/requirements.txt
pip install -e .
```

**Windows:** `python -m venv .venv` e `.\.venv\Scripts\Activate.ps1`; ou `scripts\setup.ps1`.

### Atalho local

`python scripts/installer.py` (ou `scripts/run_installer.sh` / `scripts/install.sh`) usa a mesma lógica que `./install.sh text3d` quando executado a partir de `Text3D/`.

## Uso

| Subcomando | Descrição |
|-----------|-----------|
| `text3d generate PROMPT` | Gera mesh 3D a partir de texto (Text2D → Hunyuan3D) |
| `text3d mesh-beautify` | Fusão por distância (ratio inteligente por defeito), remesh/Taubin opcional — antes do Paint3D |
| `text3d doctor` | Verifica PyTorch, VRAM e dependências nativas |
| `text3d info` | Mostra configuração, GPU, cache e ambiente |
| `text3d models` | Lista modelos disponíveis |
| `text3d convert FILE` | Converte mesh entre formatos (PLY → GLB, etc.) |
| `text3d skill install` | Instala Agent Skill Cursor no projeto |

```bash
# Mesh só geometria (Text2D → Hunyuan3D)
text3d generate "um robô futurista" -o robo.glb

# GPU com mais VRAM (equivalente ao trio HQ do model card)
text3d generate "cadeira" --preset hq -W 1024 -H 1024

# Rápido (menos passos / octree mais baixo)
text3d generate "cadeira" --preset fast -o cadeira_fast.glb

# Último recurso: Hunyuan em CPU
text3d generate "objeto" --low-vram

text3d doctor
text3d info
text3d models
text3d convert mesh.ply --output mesh.glb

# Textura num mesh já gerado (projeto Paint3D)
paint3d texture outputs/meshes/robo.glb -i minha_ref.png -o robo_tex.glb

# Unir costuras estilo Hunyuan (omitir --weld-diagonal-ratio = ratio automático ~K·aresta_média/diagonal)
text3d mesh-beautify modelo.glb -o soldado.glb --weld-only
```

### `mesh-beautify` (fusão de vértices)

Por defeito **não** passas `--weld-diagonal-ratio`: o merge em pymeshlab usa um ratio derivado da malha (**~K × comprimento médio de aresta / diagonal da AABB**, com limites). Escala com **`--weld-aggressiveness`** (defeito `1.14`). Para um ratio fixo, usa **`--weld-diagonal-ratio 0.01`**. **`--no-weld`** desliga a fusão por distância no pymeshlab.

### Textura e PBR

Fluxo completo texto → mesh → textura → mapas PBR: **[GameAssets](../GameAssets)** (`gameassets batch` com `text3d.texture` / `materialize`) ou encadear manualmente `text3d generate` → `paint3d texture` → `paint3d materialize-pbr`. Detalhes do Materialize: **[docs/PBR_MATERIALIZE.md](docs/PBR_MATERIALIZE.md)** e **[Paint3D/docs/PAINT_SETUP.md](../Paint3D/docs/PAINT_SETUP.md)**.

### Parâmetros principais (defeitos = perfil ~6 GB, validado)

Ver [`defaults.py`](src/text3d/defaults.py). Resumo:

| Flag | Padrão atual | Exemplo GPU grande (HF) |
|------|----------------|-------------------------|
| `-W` / `-H` | 768 | 1024 |
| `--steps` | 24 | 30 |
| `--guidance` | 5.0 | 5.0 |
| `--octree-resolution` | 256 | 380 |
| `--num-chunks` | 8000 | 20000 |
| `--low-vram` | off | força Hunyuan em CPU se ainda OOM |
| `--seed` | — | — |
| `--preset` | — | `fast` / `balanced` / `hq` (substitui steps+octree+chunks) |
| `--mc-level` | 0 | Iso-superfície Hunyuan (ajuste fino) |
| `--gpu-ids` | off | Split multi-GPU via accelerate (ex. `0,1`) |

## Python

```python
from text3d import HunyuanTextTo3DGenerator
from text3d.utils import save_mesh

with HunyuanTextTo3DGenerator(verbose=True) as gen:
    mesh = gen.generate(prompt="um carro vermelho")
    # Opcional (GPU grande): gen.generate(..., octree_resolution=380, num_chunks=20000, num_inference_steps=30)
    save_mesh(mesh, "carro.glb", format="glb")

# Só image-to-3D (Hunyuan)
# mesh = gen.generate_from_image("ref.png")
```

## Estrutura

```
Text3D/
├── src/text3d/
│   ├── defaults.py        # Padrões ~6GB vs constantes HQ
│   ├── generator.py       # HunyuanTextTo3DGenerator
│   ├── cli.py
│   └── utils/
│       └── env.py         # PYTORCH_CUDA_ALLOC_CONF ao iniciar o CLI
├── docs/
│   └── PBR_MATERIALIZE.md # GLB (Paint 2.1) vs PBR em imagem (Materialize)
├── config/requirements.txt

# Textura, Materialize PBR e Upscale IA → pacote Paint3D (../Paint3D)
```

## Limitações do image-to-3D e pós-processo

O Hunyuan3D gera **superfície a partir de uma vista**: geometria fina (pernas, espelhos) pode desaparecer, aparecer **várias ilhas** (pés separados) ou aspereza tipo “argila”. Por defeito o CLI aplica **pós-processo**: maior **componente conexa** (remove ilhas pequenas), **merge de vértices** e opcionalmente `--mesh-smooth N` (suavização Laplaciana).

```bash
# Mais detalhe geométrico (mais VRAM/tempo)
text3d generate "robô" --octree-resolution 256 --num-chunks 8000 --steps 28

# Suavizar ligeiramente a superfície
text3d generate "carro" --mesh-smooth 1

# Manter todas as ilhas (se precisares de peças separadas)
text3d generate "objeto" --no-mesh-repair
```

## Documentação adicional

| Ficheiro | Descrição |
|----------|-----------|
| [docs/INSTALL.md](docs/INSTALL.md) | Guia de instalação detalhado |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Resolução de problemas |
| [docs/EXAMPLES.md](docs/EXAMPLES.md) | Exemplos de uso avançado |
| [docs/API.md](docs/API.md) | Referência da API Python |
| [docs/PAINT_SETUP.md](docs/PAINT_SETUP.md) | Redireciona para Paint3D (textura Hunyuan) |
| [docs/PBR_MATERIALIZE.md](docs/PBR_MATERIALIZE.md) | PBR no GLB (Paint 2.1) vs Materialize em textura |

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `TEXT2D_MODEL_ID` | Override do modelo HF para a fase Text2D |
| `MATERIALIZE_BIN` | Não usado pelo `text3d`; opcional para **[Materialize](../Materialize)** / fluxos Texture2D |
| `HF_HOME` | Diretório de cache Hugging Face (defeito: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | Configuração CUDA (auto-definida como `expandable_segments:True` se vazia) |
| `TEXT3D_ALLOW_SHARED_GPU` | Permitir GPU partilhada com outros processos (`1` = sim) |
| `TEXT3D_GPU_KILL_OTHERS` | Controlar terminação de processos GPU concorrentes (`0` = desligar, `1` = forçar) |
| `TEXT3D_EXPORT_ROTATION_X_DEG` | Rotação X em graus ao exportar mesh (defeito: 90°, Hunyuan→Y-up) |
| `TEXT3D_EXPORT_ROTATION_X_RAD` | Alternativa em radianos |

## Créditos

- **Tencent Hunyuan3D-2.1** — [Hunyuan3D-2.1](https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1), [tencent/Hunyuan3D-2.1](https://huggingface.co/tencent/Hunyuan3D-2.1) (shape: `hunyuan3d-dit-v2-1`, SDNQ INT4)
- **Text2D** — FLUX.2 Klein (SDNQ Disty0 por defeito; opcional BF16 BFL via `TEXT2D_MODEL_ID`) no pacote `text2d` do monorepo — licenças: [GameDev/README_PT](../README_PT.md)
