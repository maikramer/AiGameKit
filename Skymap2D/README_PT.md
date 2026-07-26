# Skymap2D

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI para geração de skymaps equirectangular 360° via HF Inference API.

Usa o modelo [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) para gerar panorâmicas 360° usáveis como skybox/skymap em engines de jogo — ideal para céus, ambientes exteriores e cenários de fundo.

No monorepo [GameDev](../README_PT.md), o pacote depende de [**gamedev-shared**](../Shared/) (`gamedev_shared`): CLI Rich, instalação de skills Cursor e utilitários alinhados com Text2D/Texture2D/GameAssets.

## Características

- **Sem GPU local** — geração 100% cloud via HF Inference API
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

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh skymap2d
# Windows: .\install.ps1 skymap2d
```

Cria `Skymap2D/.venv` se necessário, instala em modo editável e gera wrappers. `./install.sh --list`. Guia: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

```bash
./scripts/setup.sh
source .venv/bin/activate
```

O `setup.sh` instala `gamedev-shared` a partir de `../Shared` e o pacote `skymap2d` em modo editável.

### Atalho local

```bash
python3 scripts/installer.py --prefix ~/.local
python3 scripts/installer.py --use-venv
```

Sem PyTorch local — apenas `config/requirements.txt` e `gamedev-shared`.

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
- **Pesos (default):** [Flux-LoRA-Equirectangular-v3](https://huggingface.co/MultiTrickFox/Flux-LoRA-Equirectangular-v3) — LoRA sobre [FLUX.1-dev](https://huggingface.co/black-forest-labs/FLUX.1-dev) (licença **não comercial** BFL); inferência via [HF Inference API](https://huggingface.co/docs/api-inference/) — aplica-se também os [termos HF](https://huggingface.co/terms-of-service).
- **Tabela completa:** [GameDev/README_PT.md](../README_PT.md) (secção Licenças).
