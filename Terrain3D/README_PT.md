# Terrain3D

**Idioma:** [English (`README.md`)](README.md) · Português

Geração de terreno por IA via [terrain-diffusion](https://github.com/millennium-nova/terrain-diffusion) (MIT). Gera heightmap PNG + terrain JSON compatível com [VibeGame](../VibeGame/) e [GameAssets](../GameAssets/).

Usa um modelo de difusão treinado em dados de elevação reais (WorldClim + ETOPO) para produzir terreno realista com montanhas, vales e cristas — sem edição manual.

## Funcionalidades

- **Terreno por IA** — heightmap via difusão (~30 m de resolução)
- **Reprodutibilidade por seed** — mesma seed → mesmo terreno
- **Heightmap PNG** — grayscale 8-bit, normalizado 0–1
- **JSON de metadados** — versão 2.0, compatível com a pipeline VibeGame/GameAssets
- **Condicionamento WorldClim** — mapas bioclimáticos sintéticos para elevação realista
- **Download automático** — rasters bioclimáticos baixados na primeira execução

## Requisitos

- Python 3.10+
- PyTorch 2.4+ (CUDA necessário)
- ~6 GB de VRAM
- Acesso à rede (download do modelo na primeira execução)

## Instalação

### Oficial (monorepo)

Na raiz do repositório **GameDev**:

```bash
cd /caminho/para/GameDev
./install.sh terrain3d
```

Guia geral: [docs/INSTALLING.md](../docs/INSTALLING.md).

### Manual / desenvolvimento

```bash
cd Terrain3D
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
terrain3d --help
```

## Uso

### Gerar terreno

```bash
terrain3d generate --seed 42 --size 1024
terrain3d generate --seed 100 --size 2048 --output meu_terreno.png
terrain3d generate --size 1024 --max-height 100 --world-size 1024
```

### Parâmetros

| Parâmetro | Padrão | Descrição |
|-----------|--------|-----------|
| `--seed` | aleatório | Seed para reprodutibilidade |
| `--size` | 2048 | Resolução do heightmap em pixels |
| `--output` | `heightmap.png` | Caminho do PNG de saída |
| `--metadata` | `terrain.json` | Caminho do JSON de metadados |
| `--world-size` | 512.0 | Extensão do mundo em metros (X/Z) |
| `--max-height` | 50.0 | Altura máxima do terreno em metros |
| `--device` | auto | Dispositivo (`cuda`, `cpu`) |
| `--dtype` | fp32 | Precisão do modelo (`fp32`, `bf16`, `fp16`) |
| `--cache-size` | 100M | Tamanho do cache de tiles |
| `--coarse-window` | 4 | Número de tiles coarse (~7.7 km cada) |
| `--prompt` | nenhum | Descrição do terreno (apenas metadados; modelo é incondicional) |
| `--quiet` | desligado | Suprimir saída de progresso |

### Informações

```bash
terrain3d --help
terrain3d --version
```

## Saída

### heightmap.png

PNG grayscale 8-bit. Valores de pixel 0–255 mapeiam para elevação 0–1 (normalizado).

### terrain.json

```json
{
  "version": "2.0",
  "generator": "terrain3d",
  "model_id": "xandergos/terrain-diffusion-30m",
  "terrain": {
    "size": 1024,
    "world_size": 512.0,
    "max_height": 50.0,
    "height_min": 0.0,
    "height_max": 1.0,
    "height_mean": 0.56,
    "height_std": 0.18
  },
  "rivers": [],
  "lakes": [],
  "lake_planes": [],
  "stats": {
    "generation_time_seconds": 108.2
  }
}
```

## Estrutura

```
Terrain3D/
├── src/terrain3d/
│   ├── cli.py                 # CLI Click
│   ├── cli_rich.py            # Rich-click + tema
│   ├── generator.py           # Wrapper WorldPipeline
│   ├── export.py              # Exportação PNG + JSON
│   └── vendor/                # Código vendored terrain-diffusion (MIT)
│       ├── inference/         # WorldPipeline, mapas sintéticos, pós-processamento
│       ├── models/            # EDM UNet, camadas MP
│       ├── scheduler/         # Scheduler DPM-Solver
│       ├── data/              # Encoder Laplaciano
│       ├── common/            # Helpers compartilhados
│       └── data/global/       # Rasters WorldClim + ETOPO
├── scripts/
│   └── installer.py           # Instalador do pacote
├── tests/
├── pyproject.toml
└── THIRD_PARTY.md             # Licenças do código vendored
```

## Variáveis de ambiente

| Variável | Descrição |
|----------|-----------|
| `TERRAIN3D_MODEL_ID` | Sobrescrever modelo padrão (`xandergos/terrain-diffusion-30m`) |
| `TERRAIN3D_BIN` | Caminho para o binário `terrain3d` (para GameAssets) |
| `HF_HOME` | Diretório de cache do Hugging Face |

## Integração com GameAssets

[GameAssets](../GameAssets/) pode chamar `terrain3d` durante a geração em batch. Use `TERRAIN3D_BIN` se o comando não estiver no `PATH`.

## Desenvolvimento

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Código vendored (terrain-diffusion):** MIT — [THIRD_PARTY.md](THIRD_PARTY.md).
- **Pesos do modelo:** [xandergos/terrain-diffusion-30m](https://huggingface.co/xandergos/terrain-diffusion-30m) — verifique o model card para termos de licença.
- **Dados WorldClim:** [worldclim.org](https://worldclim.org/) — gratuito para pesquisa e uso não-comercial.
