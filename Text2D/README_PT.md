# Text2D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI de **text-to-imagem** com [FLUX.2 Klein 4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) em quantização **SDNQ** ([Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic)), no mesmo espírito do Text3D (Click + Rich, `src/`, scripts).

## Requisitos

| Item    | Mínimo | Notas |
|---------|--------|--------|
| Python  | 3.13+  | Fixado `>=3.13,<3.14` (`pyproject.toml`) |
| GPU     | Opcional | NVIDIA + CUDA recomendado para inferência razoável |
| VRAM    | ~6 GB+ com hw-auto e 512² | GPUs modestas: hw-auto (defeito) escolhe 4B + CPU offload; multi-GPU com `--gpu-ids` |

Auto-detecção de hardware (`--hw-auto`, ligada por defeito): GPUs <7.5 GB ganham CPU offload + modelo 4B automaticamente; rigs grandes mantêm 9B/full-GPU/multi-GPU. Flags explícitas ganham. Desligar: `--no-hw-auto` ou `TEXT2D_HW_AUTO=0`. Ver perfil: `text2d doctor`.
| Disco   | ~8 GB  | Cache HF + pesos SDNQ (~2,5 GB em disco) |

**Licença dos pesos:** o default é a base oficial [black-forest-labs/FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) (**Apache 2.0**, download público) / [9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) (**gated** — aceitar termos BFL + `HF_TOKEN`) com quantização **SDNQ em runtime** (MIT, [Disty0/sdnq](https://github.com/Disty0/sdnq)). Checkpoints pré-quantizados [Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) são opcionais via `TEXT2D_MODEL_ID` (declaram `flux-non-commercial-license`). Resumo: [Licenças no monorepo](../README_PT.md).

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório AiGameKit (pasta com `install.sh` e `Shared/`):

```bash
cd /caminho/para/AiGameKit
./install.sh text2d
```

Equivalente: `aigamekit-install text2d`. Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento (`scripts/setup.sh`)

`setup.sh` **não** substitui o instalador oficial; é conveniência para criar `Text2D/.venv` e `pip install -e` localmente.

```bash
cd Text2D
chmod +x scripts/setup.sh
./scripts/setup.sh
source .venv/bin/activate
text2d --help
```

- Com **NVIDIA**, `setup.sh` instala PyTorch com CUDA (wheels do **PyPI** em **Python 3.13**).
- Dependências de runtime: [`config/requirements.txt`](config/requirements.txt). Desenvolvimento/testes: [`config/requirements-dev.txt`](config/requirements-dev.txt) ou `pip install -e ".[dev]"`.

### Atalho local (`scripts/installer.py`)

Com `.venv` já criado (ex.: após `setup.sh`):

```bash
chmod +x scripts/run_installer.sh scripts/install.sh
./scripts/run_installer.sh --use-venv --prefix ~/.local
# ou: ./scripts/install.sh … (delega para run_installer.sh)
```

Instalação a partir do `python3` do sistema (PyTorch + requirements + pacote + wrappers em `PREFIX/bin`):

```bash
python3 scripts/installer.py --prefix ~/.local
```

Opções: `--use-venv`, `--skip-deps`, `--skip-models`, `--force`, `--prefix`, `--python`. Sem `.venv` e com `--use-venv`, o instalador **termina com erro** (crie o venv primeiro).

Documentação detalhada: [docs/INSTALL.md](docs/INSTALL.md). Problemas de GPU/carregamento: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Primeira geração vs seguintes

- **1.ª execução:** descarga de vários GB do Hugging Face — pode levar **vários minutos**; a GPU pode mostrar **0%** durante rede/disco (normal).
- **Com cache local:** o mesmo comando costuma ficar na ordem de **segundos a ~1 min** (carregar do disco + inferência), conforme hardware.

## Uso

| Subcomando | Descrição |
|-----------|-----------|
| `text2d generate PROMPT` | Gera uma imagem a partir de texto |
| `text2d info` | Mostra configuração e ambiente (GPU, cache, modelo) |
| `text2d models` | Lista modelos disponíveis |
| `text2d skill install` | Instala Agent Skill Cursor no projeto |

```bash
text2d generate "um gato com um cartaz que diz olá mundo"

text2d generate "paisagem ao pôr do sol" --width 768 --height 768 --steps 4 --guidance 1.0

text2d generate "retrato" -o minha.png --seed 42   # hw-auto activo por defeito

# Multi-GPU: dividir modelo entre GPUs 0 e 1
text2d generate "retrato" --gpu-ids 0,1 -o minha.png

text2d generate "teste" -v          # --verbose no próprio subcomando
text2d -v generate "teste"          # ou verbose no grupo

# Kernel (opt-in em generate; ON por defeito em generate-batch + vramd)
text2d generate "retrato" --compile --channels-last -o hot.png
text2d generate-batch manifest.json -O out/   # compile+CL já ON

text2d info
text2d models
```

Kernel opts (~6 GB): [`docs/findings/KERNEL_OPTS_FINDINGS.md`](../docs/findings/KERNEL_OPTS_FINDINGS.md).

### Variáveis de ambiente

| Variável | Descrição |
|----------|-----------|
| `TEXT2D_MODEL_ID` | Repositório HF alternativo compatível com `Flux2KleinPipeline` (ex.: `black-forest-labs/FLUX.2-klein-4B` para Apache 2.0; default = base BFL fp16 + SDNQ runtime) |
| `HF_HOME` | Cache Hugging Face (por defeito: `~/.cache/huggingface`) |
| `TEXT2D_MODELS_DIR` | Diretório de modelos locais; o instalador grava em `~/.config/text2d/config.env` quando existe `Text2D/models/` com pesos |
| `TEXT2D_OUTPUT_DIR` | Diretório de saída das imagens (criado pelo instalador em `~/.text2d/outputs`) |
| `PYTORCH_CUDA_ALLOC_CONF` | Configuração CUDA (auto-definida se vazia) |
| `VRAMD_AUTO_START` | `0` desliga o auto-start do vramd |

### Guidance

A base oficial FLUX.2 Klein usa **guidance 1.0** por defeito (compatível com o SDNQ runtime); o BF16 original BFL costuma aceitar valores mais altos (ex. 4.0).

## Unified Model Server (vramd)

`text2d generate` delega automaticamente no **`vramd`** — o supervisor GPU do monorepo (um processo, um socket, fila com prioridade + afinidade VRAM, evicção peso + LRU, workers subprocess por tool). Auto-arranca no primeiro generate salvo `VRAMD_AUTO_START=0`.

```bash
text2d generate "gato" -o gato.png --vramd-stream          # eventos de fila/progresso
text2d generate "gato" -o gato.png --vramd-priority batch
text2d generate "gato" -o gato.png --no-vramd              # forçar in-process
vramd status                                               # backends + HOLDING/QUEUE
vramd respawn text2d                                       # recarrega código src/ editado
```

Após editar `*/src/`: `vramd respawn text2d`. Guia completo: [`Vramd/README.md`](../Vramd/README.md).

## GGUF / Unsloth

Pesos **GGUF** destinam-se a fluxos **ComfyUI-GGUF**, não a este CLI (Diffusers).

## Estrutura

```
Text2D/
├── src/text2d/
│   ├── cli.py             # CLI Click (generate, info, models)
│   ├── generator.py       # Pipeline FLUX + inferência
│   ├── cli_rich.py        # Configuração Rich para o CLI
│   └── utils/             # Utilitários (paths, etc.)
├── docs/
│   ├── INSTALL.md         # Guia de instalação detalhado
│   └── TROUBLESHOOTING.md # Resolução de problemas
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh           # Setup do venv + deps
│   ├── run_installer.sh   # Chama installer.py (implementação)
│   ├── install.sh         # Delega para run_installer.sh (atalho local)
│   └── installer.py       # Lógica partilhada com aigamekit-install
└── tests/
```

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Pesos:** default = base oficial BFL fp16 ([FLUX.2-klein-4B](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B) Apache 2.0, público; [9B](https://huggingface.co/black-forest-labs/FLUX.2-klein-9B) **gated**) + quantização SDNQ runtime (MIT, [Disty0/sdnq](https://github.com/Disty0/sdnq)). Mirrors pré-quantizados [Disty0](https://huggingface.co/Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic) declaram `flux-non-commercial-license`. Tabela completa: [AiGameKit/README_PT.md — Licenças](../README_PT.md).
