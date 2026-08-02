# Skymap2D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI para geração de skymaps equirectangular 360° com FLUX.1-dev + LoRA, localmente na GPU.

Usa o modelo [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) para gerar panorâmicas 360° usáveis como skybox/skymap em engines de jogo — ideal para céus, ambientes exteriores e cenários de fundo.

No monorepo [AiGameKit](../README_PT.md), o pacote depende de [**aigamekit-shared**](../Shared/) (`aigamekit_shared`): CLI Rich, instalação de skills Cursor e utilitários alinhados com Text2D/Texture2D/GameAssets.

## Características

- **Inferência local na GPU** — FLUX.1-dev + LoRA via `diffusers` (requer CUDA)
- **Prompt equirectangular automático** — acrescenta instruções 360°/equirectangular automaticamente
- **10 presets de ambiente** — Sunset, Night Sky, Overcast, Clear Day, Storm, Space, etc.
- **Batch** — gera múltiplos skymaps a partir de um ficheiro de prompts
- **Metadata JSON** — cada skymap acompanha ficheiro `.json` com seed, prompt final, parâmetros
- **Ratio 2:1** — defaults optimizados (2048×1024) para projeção equirectangular
- **Saída EXR (opcional)** — RGB float32 em espaço **linear** (OpenEXR), para motores que preferem `.exr`. O modelo continua a devolver LDR; o EXR empacota o mesmo conteúdo sem segunda curva sRGB. *Não* usamos o [Materialize](../Materialize/) aqui: esse fluxo gera mapas PBR (normal, height, …) a partir de texturas; para panoramas basta o `skymap2d` com `--format exr`.

## Arranque rápido

```bash
# 1. Setup (venv + deps)
./scripts/setup.sh

# 2. Ativar
source .venv/bin/activate

# 3. Gerar
skymap2d generate "sunset over mountains, warm golden light" -o sky_sunset.png

# 4. Usar preset
skymap2d generate "dramatic sky" --preset Storm -o sky_storm.png

# 5. EXR (RGB linear) em vez de PNG
skymap2d generate "clear blue sky" --format exr -o sky_clear.exr
# ou: -o sky_clear.exr  (a extensão .exr define o formato)
```

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório AiGameKit:

```bash
cd /caminho/para/AiGameKit
./install.sh skymap2d
# Windows: .\install.ps1 skymap2d
```

Cria `Skymap2D/.venv` se necessário, instala em modo editável e gera wrappers. `./install.sh --list`. Guia: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

```bash
./scripts/setup.sh
source .venv/bin/activate
```

O `setup.sh` instala `aigamekit-shared` a partir de `../Shared` e o pacote `skymap2d` em modo editável.

### Atalho local

```bash
python3 scripts/installer.py --prefix ~/.local
python3 scripts/installer.py --use-venv
```

Requer PyTorch/CUDA — FLUX.1-dev + LoRA correm localmente via `diffusers` (`config/requirements.txt` + `aigamekit-shared`).

## Comandos

| Comando | Descrição |
|---------|-----------|
| `skymap2d generate PROMPT` | Gera um skymap equirectangular 360° |
| `skymap2d presets` | Lista presets de ambiente |
| `skymap2d batch FILE` | Batch a partir de ficheiro (um prompt por linha) |
| `skymap2d info` | Configuração e ambiente |
| `skymap2d skill install` | Instala Agent Skill Cursor |

## Parâmetros de `generate`

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `--output/-o` | auto | Ficheiro de saída (`.png` ou `.exr`) |
| `--format` | png | `png` ou `exr` (se `-o` não tiver extensão, usa isto) |
| `--exr-scale` | 1.0 | Multiplica valores lineares ao gravar EXR |
| `--width/-W` | 2048 | Largura (ratio 2:1 recomendado) |
| `--height/-H` | 1024 | Altura |
| `--steps/-s` | 40 | Passos de inferência (10–100) |
| `--guidance/-g` | 6.0 | Guidance scale (1.0–20.0) |
| `--seed` | aleatório | Seed para reprodutibilidade |
| `--negative-prompt/-n` | "" | Prompt negativo |
| `--preset/-p` | None | Preset de ambiente |
| `--cfg-scale` | guidance | CFG scale |
| `--lora-strength` | 1.0 | Força do LoRA (0.0–2.0) |
| `--model/-m` | Flux-LoRA-Equirectangular-v3 | Modelo HF |
| `--compile/--no-compile` | off (`generate`); **on** (`batch`) | `torch.compile` (~−19% hot); cold ~6 min — off em one-shot |
| `--channels-last` | off | NHWC; pouco ganho nestes benches |

Kernel opts (batch/UMS): [`docs/findings/KERNEL_OPTS_FINDINGS.md`](../docs/findings/KERNEL_OPTS_FINDINGS.md).

## Presets

| Nome | Descrição |
|------|-----------|
| Sunset | Céu ao pôr do sol, nuvens douradas |
| Night Sky | Noite estrelada, Via Láctea |
| Overcast | Céu nublado, luz difusa |
| Clear Day | Céu limpo azul, poucas nuvens |
| Storm | Tempestade, nuvens escuras, relâmpagos |
| Space | Espaço exterior, nebulosa, estrelas |
| Alien World | Céu alienígena, duas luas, cores fantásticas |
| Dawn | Amanhecer, tons rosa e laranja |
| Underwater | Vista subaquática, raios de luz, água |
| Fantasy | Céu mágico, auroras, cristais flutuantes |

## Unified Model Server (UMS)

`skymap2d generate` delega automaticamente no **`aigamekit-model-server`** — o supervisor GPU do monorepo (um processo, um socket, fila com prioridade + afinidade VRAM, evicção peso + LRU, workers subprocess por tool). Auto-arranca no primeiro generate salvo `AIGAMEKIT_UMS_AUTO_START=0`.

```bash
skymap2d generate "pôr do sol" -o ceu.png --ums-stream
skymap2d generate "noite" -o ceu_noite.png --ums-priority batch
skymap2d generate "tempestade" -o ceu_storm.png --no-ums   # forçar in-process
ums status
ums respawn skymap2d                                       # recarrega código src/ editado
```

Modelo: base [FLUX.1-dev SDNQ uint4](https://huggingface.co/Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32) (mirror público) + [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) (público). A base oficial `black-forest-labs/FLUX.1-dev` é **gated** — usar `SKYMAP2D_BASE_MODEL_ID` só depois de aceitar os termos BFL. Guia completo: [`ModelServer/README.md`](../ModelServer/README.md).

## Configuração

| Variável | Descrição |
|----------|-----------|
| `HF_TOKEN` | Token Hugging Face (ou `HUGGINGFACEHUB_API_TOKEN`) |
| `SKYMAP2D_MODEL_ID` | Override do modelo (default: `MultiTrickFox/Flux-LoRA-Equirectangular-v3`) |

## Uso em engines de jogo

O skymap equirectangular gerado pode ser usado directamente como:
- **Godot**: Environment → Sky → PanoramaSky → panorama texture
- **Unity**: Skybox material com shader Panoramic → assign texture
- **Unreal Engine**: Sky Sphere → equirectangular texture map

## Testes

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Pesos (default):** [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — LoRA sobre [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) (licença **não comercial** BFL); inferência local com `diffusers` — pesos descarregados do HF Hub (aplicam-se os [termos HF](https://huggingface.co/terms-of-service)).
- **Tabela completa:** [AiGameKit/README_PT.md](../README_PT.md) (secção Licenças).
