# Text2Icon

CLI text-to-icon com **Sana** ([NVlabs/Sana](https://github.com/NVlabs/Sana)) — gera ícones de UI para jogos (20 passos, pipeline 512px). `hw_auto` escolhe automaticamente o transformer e a quantização SDNQ conforme a VRAM disponível (ver [Modelos](#modelos)). Suporta fundo transparente (RGBA via `rembg`).

Parte do monorepo **GameDev**. Veja [`../AGENTS.md`](../AGENTS.md) para o guia geral.

## Instalação

```bash
# No monorepo (depois de instalar Shared/)
./install.sh text2icon
# ou
cd Text2Icon && pip install -e ".[dev]"
```

Requer Python 3.13, CUDA (ou CPU, lento) e ~2.5 GB de cache HF para o modelo.

## Uso

```bash
# Ícone opaco (RGB)
text2icon generate "red health potion, fantasy game" -o health.png

# Ícone transparente (RGBA via rembg/U2Net)
text2icon generate "sword icon, medieval RPG" -o sword.png --transparent

# Batch (um prompt por linha)
text2icon batch icons.txt -d icons/ --transparent --quality medium

# Forçar o fallback ternário (hardware modesto, ≤4 GB VRAM)
text2icon generate "shield emblem" -m clark-labs/clark-air-sana-1.6b-1.58bit

# Escolher manualmente a quantização do transformer (em vez de hw_auto)
text2icon generate "shield emblem" --quant-transformer sdnq-fp8
```

## Opções

| Flag | Default | Descrição |
|------|---------|-----------|
| `-o/--output` | `outputs/icons/<slug>_<ts>.png` | Ficheiro de saída |
| `-W/--width` | `512` | Largura (múltiplo de 8) |
| `-H/--height` | `512` | Altura (múltiplo de 8) |
| `-s/--steps` | `2` | Passos de inferência (Sprint: 1–4) |
| `-g/--guidance` | `4.5` | Guidance scale (CFG) |
| `--seed` | aleatório | Seed reprodutível |
| `-n/--negative-prompt` | `""` | Prompt negativo |
| `--transparent/--no-transparent` | off | Remover fundo (rembg/U2Net) |
| `-m/--model` | hw_auto | ID do transformer HF (ver Modelos) |
| `--quant-encoder` | `auto` | SDNQ do Gemma text encoder (auto/sdnq-int4/sdnq-int8/none) |
| `--quant-transformer` | `auto` | SDNQ do transformer principal (auto/sdnq-int4/sdnq-uint4/sdnq-int8/sdnq-uint8/sdnq-fp8/none) |
| `--cpu` | off | Forçar CPU |
| `--low-vram` | auto | CPU offload |
| `--gpu-ids` | auto | Split multi-GPU (ex: `0,1`) |
| `--quality` | `medium` | Tier de qualidade (fast/low/medium/high/highest) |
| `--hw-auto/--no-hw-auto` | on | Auto-detecção de hardware (transformer + SDNQ + offload + clamp) |
| `--ums-priority` | interactive / env | Prioridade na fila UMS (`interactive` \| `batch`) |
| `--no-ums` | off | Forçar geração in-process (ignorar UMS) |
| `--ums-stream` | off | Mostrar eventos de fila/progresso do UMS |

## Unified Model Server (UMS)

Preferir `gamedev-model-server` (supervisor do monorepo). `text2icon generate`
delega automaticamente (auto-start salvo `GAMEDEV_UMS_AUTO_START=0`).

```bash
gamedev-model-server start
text2icon generate "sword icon" -o sword.png --ums-stream
text2icon server   # deprecated — fallback per-tool apenas
```

Ver [`ModelServer/README.md`](../ModelServer/README.md).

## Modelos

`hw_auto` escolhe o transformer e a quantização SDNQ por VRAM (maior GPU disponível):

| VRAM | Transformer | SDNQ transformer | Offload/clamp |
|------|-------------|-------------------|----------------|
| ≥ 10 GiB | `Efficient-Large-Model/Sana_600M_512px_diffusers` (standard) | nenhuma (bf16/fp16, "16-bit") | não |
| ≥ 8 GiB | standard | `sdnq-uint8` ("8-bit") | não |
| ≥ 6 GiB | standard | `sdnq-uint8` | offload |
| ≥ 4 GiB | standard | `sdnq-int4` ("4-bit") | offload + clamp 512×512 |
| < 4 GiB (ou CPU) | `clark-labs/clark-air-sana-1.6b-1.58bit` (ternário) | nenhuma (já ~1.85 bits/weight no checkpoint) | offload + clamp 512×512 |

O transformer ternário é um fallback dedicado a hardware modesto; o SDNQ não se aplica a ele (já vem pré-comprimido). `--quant-encoder`/`--quant-transformer` e `-m` explícitos sempre ganham a `hw_auto`.

## Integração com GameAssets

`text2icon` está integrado no pipeline do `gameassets` (stage scene-level, como o `skymap2d`). Adiciona um bloco `text2icon:` ao `game.yaml`:

```yaml
text2icon:
  prompts:
    - "health potion icon"
    - "mana potion icon"
    - "sword icon"
  transparent: true
  width: 256
  height: 256
```

Depois `gameassets batch` gera os ícones e `gameassets handoff` copia-os para `public/assets/icons/`.

## Variáveis de ambiente

| Variável | Efeito |
|----------|--------|
| `TEXT2ICON_MODEL_ID` | Override do modelo default |
| `TEXT2ICON_BIN` | Caminho do binário (usado pelo GameAssets) |
| `TEXT2ICON_HW_AUTO` | `0` desliga a auto-detecção de hardware |
| `HF_HOME` | Cache do Hugging Face |

## Testes

```bash
make test-text2icon
# ou
cd Text2Icon && pytest tests/
```

Os testes são CPU-only e fazem mock do torch/Sana (sem download de modelo).

## Licença

MIT. Modelo Sana: ver [NVlabs/Sana](https://github.com/NVlabs/Sana).
