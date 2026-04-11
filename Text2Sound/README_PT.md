# Text2Sound

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI para geração de áudio estéreo a 44.1 kHz a partir de prompts de texto, com dois checkpoints Hugging Face (mesma biblioteca `stable-audio-tools`):

| Uso | Modelo | Duração máx. | Comando |
|-----|--------|----------------|---------|
| Música / ambientes longos | [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) | ~47 s | `--profile music` (padrão) |
| Efeitos / SFX curtos | [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) | ~11 s | `--profile effects` |

Ambos os repositórios podem ser **gated**: aceita os termos no Hub e define `HF_TOKEN`. **Stability AI Community License** (`LICENSE.md` em cada repo): pesquisa e uso não comercial; uso comercial com **teto de receita** (ver texto atual no repositório e [stability.ai/license](https://stability.ai/license)). Resumo: [GameDev/README_PT.md — Licenças](../README_PT.md).

## Funcionalidades

- **Dois perfis** — `music` (Open 1.0, até ~47 s) e `effects` (Open Small, até ~11 s; defaults ~8 steps, CFG 1.0, sampler `pingpong`)
- **Geração text-to-audio** — áudio estéreo conforme o modelo escolhido
- **Presets para game dev** — ambient, battle, menu, footsteps, weather, UI, nature, dungeon, tavern, etc.
- **Múltiplos formatos** — WAV, FLAC, OGG
- **Batch processing** — gerar vários áudios a partir de ficheiro de prompts
- **Seed** — reprodutibilidade total
- **Trim automático** — remoção de silêncio no início e no fim
- **Metadados JSON** — parâmetros de geração gravados ao lado do áudio (inclui `seed_effective`, sigmas, trim, versão da CLI)
- **Gestão de VRAM** — limpeza automática após cada geração

## Requisitos

- Python 3.10+
- PyTorch 2.1+ (CUDA recomendado)
- ~4 GB de VRAM (geração em GPU)
- Token HF (se o modelo exigir autenticação): `HF_TOKEN`

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh text2sound
```

Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

```bash
cd Text2Sound
bash scripts/setup.sh
source .venv/bin/activate
```

### Atalho local

```bash
python3 scripts/installer.py --use-venv
```

## Uso

### Gerar áudio

```bash
text2sound generate "ocean waves crashing on a sandy beach at sunset"
text2sound generate "epic orchestral battle music" --duration 45 --steps 120
text2sound generate "short alien laser shot" --profile effects -d 1.5
text2sound generate "footsteps on gravel" -d 5 -s 80 --format flac
text2sound generate "rain and thunder" --seed 42 --cfg-scale 8
```

### Modelo e aliases

- **`--profile music`** (padrão): `stabilityai/stable-audio-open-1.0`
- **`--profile effects`**: `stabilityai/stable-audio-open-small`
- **`--model`** tem prioridade sobre o perfil: aceita o ID HF completo ou aliases `music`, `full`, `effects`, `small`, `sfx`

```bash
text2sound generate "loop" --model small -d 8
text2sound generate "score" --model music -d 30
```

### Usar presets

```bash
text2sound presets                          # listar presets disponíveis
text2sound generate --preset battle ""      # usar preset diretamente
text2sound generate --preset ambient "with gentle river flowing"  # preset + custom
```

### Batch

O diretório de saída usa **`-O` / `--output-dir`** (em `generate`, `-d` é sempre **duração**).

```bash
# Ficheiro prompts.txt (um prompt por linha, # = comentário)
text2sound batch prompts.txt --format flac -O sounds/
text2sound batch prompts.txt --seed 1000   # seeds 1000, 1001, 1002, … por linha
```

### Informações

```bash
text2sound info     # ambiente, GPU, modelo, configuração
text2sound --help   # ajuda completa
```

## Presets disponíveis

| Preset | Tipo | Duração |
|--------|------|---------|
| ambient | Ambiente calmo | 45s |
| battle | Música de combate | 30s |
| menu | Música de menu | 30s |
| footsteps-stone | Passos em pedra | 5s |
| footsteps-grass | Passos em relva | 5s |
| rain | Chuva com trovão | 45s |
| wind | Vento forte | 30s |
| thunder | Trovão isolado | 8s |
| ui-click | Click de interface | 2s |
| ui-confirm | Confirmação | 3s |
| forest | Floresta | 45s |
| ocean | Ondas do mar | 45s |
| dungeon | Dungeon escura | 30s |
| tavern | Taverna medieval | 30s |
| explosion | Explosão | 5s |
| sword-clash | Espadas | 3s |
| magic-spell | Magia | 4s |
| victory-fanfare | Fanfarra de vitória | 8s |

## Parâmetros avançados

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `--duration` | 30 | Duração em segundos (máx. ~47 música, ~11 efeitos; mín. 0.5) |
| `--steps` | 100 | Passos de difusão (8–150; efeitos ~8) |
| `--cfg-scale` | 7.0 | Classifier-free guidance (1–15) |
| `--sigma-min` | 0.3 | Noise schedule mínimo |
| `--sigma-max` | 500 | Noise schedule máximo |
| `--sampler` | dpmpp-3m-sde | Tipo de sampler |
| `--seed` | aleatório | Seed para reprodutibilidade |
| `--trim/--no-trim` | trim | Remover silêncio no início e no fim |

## Estrutura

```
Text2Sound/
├── src/text2sound/
│   ├── cli.py             # CLI Click (generate, batch, presets, info)
│   ├── generator.py       # Pipeline Stable Audio Open
│   ├── presets.py         # Presets de áudio para game dev
│   ├── audio_processor.py # Processamento de áudio (trim, etc.)
│   └── utils.py           # Utilitários
├── config/
│   ├── requirements.txt
│   └── requirements-dev.txt
├── scripts/
│   ├── setup.sh           # Setup do venv + deps
│   └── installer.py       # Instalador standalone
└── tests/
```

## Variáveis de Ambiente

| Variável | Descrição |
|----------|-----------|
| `HF_TOKEN` | Token Hugging Face para autenticação (ou `HUGGINGFACEHUB_API_TOKEN`) |
| `HF_HOME` | Diretório de cache Hugging Face (padrão: `~/.cache/huggingface`) |
| `PYTORCH_CUDA_ALLOC_CONF` | Configuração de alocação CUDA (auto-definida se vazia) |

## Integração com GameAssets

O [GameAssets](../GameAssets/) pode invocar `text2sound` automaticamente durante um batch:

1. No `manifest.csv`, adicionar coluna **`generate_audio`** com valor `true` nas linhas desejadas.
2. No `game.yaml`, configurar o bloco **`text2sound`** (duração, passos, formato, etc.).
3. Correr `gameassets batch` — o áudio é gerado após a imagem 2D de cada linha.

```bash
# GameAssets invoca text2sound por linha com generate_audio=true
gameassets batch --profile game.yaml --manifest manifest.csv
```

Variável `TEXT2SOUND_BIN` se o comando não estiver no `PATH`.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest tests/ -v
# Excluir testes lentos (carregam modelo/GPU):
pytest tests/ -v -m "not slow"
```

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Pesos:** [Stable Audio Open 1.0](https://huggingface.co/stabilityai/stable-audio-open-1.0) e [Stable Audio Open Small](https://huggingface.co/stabilityai/stable-audio-open-small) — **Stability AI Community License** (aceitar no Hub; condições de uso comercial e limite de receita no `LICENSE.md` de cada repositório e em [stability.ai/license](https://stability.ai/license)).
