# TerrainGen

**Documentação:** [English (`README.md`)](README.md) · Português (esta página)

CLI para **geração procedural de terrenos** — heightmaps diamond-square, erosão hidráulica, rios baseados em fluxo e posicionamento de lagos.

Gera PNGs de heightmap em escala de cinza 8-bit (2048x2048) e metadata JSON com caminhos de rios, posições de lagos e estatísticas de geração. Desenhado para a engine 3D VibeGame e para o pipeline `gameassets dream`.

No monorepo [GameDev](../README_PT.md), o pacote depende de [**gamedev-shared**](../Shared/) (`gamedev_shared`): CLI Rich, logging e utilitários alinhados com as restantes ferramentas GameDev.

## Características

- **Diamond-square** — geração de heightmap fractal com roughness configurável, portado de [grkvlt/landscape](https://github.com/grkvlt/landscape)
- **Suavização por gradiente** — achata áreas de baixo gradiente em planícies naturais
- **Erosão hidráulica** — simulação por partículas que esculpe vales e deposita sedimento (algoritmo de Ivo van der Veen)
- **Extração de rios** — acumulação de fluxo D8 via [whitebox](https://github.com/jblindsay/whitebox-tools) com escavação de vales
- **Geração de lagos** — identificação de depressões (Planchon-Darboux) com decomposição em planos de água para VibeGame
- **Determinístico** — a mesma seed produz saída idêntica em bytes todas as vezes

## Arranque rápido

```bash
# 1. Setup (venv + deps)
./scripts/setup.sh

# 2. Ativar
source .venv/bin/activate

# 3. Gerar um terreno
terraingen generate --seed 42 -o heightmap.png --metadata terrain.json

# 4. Gerar com prompt (guardado como metadata para gameassets dream)
terraingen generate --prompt "ilha montanhosa" --seed 42
```

## Instalação

### Oficial (monorepo)

Na **raiz** do repositório GameDev:

```bash
cd /caminho/para/GameDev
./install.sh terraingen
# Windows: .\install.ps1 terraingen
```

O instalador **cria** `TerrainGen/.venv` se não existir, instala em modo editável e gera wrappers em `~/.local/bin`. Lista de ferramentas: `./install.sh --list`. Guia geral: [docs/INSTALLING_PT.md](../docs/INSTALLING_PT.md) · [EN](../docs/INSTALLING.md)

### Manual / desenvolvimento

```bash
cd TerrainGen
pip install -e ".[dev]"
```

Dependências: `numpy`, `pillow`, `whitebox`, `tifffile`, `click`, `rich`, `rich-click`, `gamedev-shared`.

## Comandos

| Comando | Descrição |
|---------|-----------|
| `terraingen generate` | Gera um heightmap procedural de terreno |

## Opções de `generate`

| Opção | Default | Descrição |
|-------|---------|-----------|
| `--prompt` | None | Descrição do terreno (guardada como metadata) |
| `--seed` | aleatório | Seed aleatória para reprodutibilidade |
| `--output/-o` | heightmap.png | Caminho de saída do PNG |
| `--metadata` | terrain.json | Caminho de saída do JSON |
| `--size` | 2048 | Resolução do heightmap (px) |
| `--world-size` | 256.0 | Tamanho do mundo em metros |
| `--max-height` | 50.0 | Altura máxima do terreno em metros |
| `--roughness` | 2.0 | Roughness do diamond-square |
| `--erosion-particles` | 50000 | Número de partículas de erosão |
| `--river-threshold` | 1000 | Limiar de acumulação de fluxo para rios |
| `--no-erosion` | off | Saltar etapa de erosão |
| `--no-rivers` | off | Saltar extração de rios |
| `--no-lakes` | off | Saltar geração de lagos |
| `--quiet` | off | Suprimir output de progresso |

### Exemplos

```bash
# Mínimo: seed aleatória, definições por omissão
terraingen generate

# Determinístico com resolução personalizada
terraingen generate --seed 42 --size 1024 --world-size 128 --max-height 30

# Pré-visualização rápida (saltar etapas pesadas)
terraingen generate --no-erosion --no-rivers --no-lakes --quiet

# Alta detalhe com ajuste de erosão
terraingen generate --seed 7 --erosion-particles 100000 --river-threshold 500
```

## Pipeline

O pipeline de geração corre por ordem:

1. **Diamond-square** — heightmap fractal a partir de uma grelha pequena, dobrando a cada iteração
2. **Suavização** — filtro low-pass baseado em gradiente, achata planícies preservando cristas
3. **Erosão** (opcional) — simulação por partículas: gotas em pontos altos, erode/deposita sedimento, evapora
4. **Rios** (opcional) — acumulação de fluxo D8, extração acima do limiar, escavação de vales Gaussiana
5. **Lagos** (opcional) — preenchimento de depressões, filtragem por componentes conexos, decomposição em planos de água
6. **Exportação** — PNG em escala de cinza 8-bit + metadata JSON

## Saída

Dois ficheiros por geração:

- **Heightmap PNG** — escala de cinza 8-bit, `size x size` pixéis, modo `L`. Linha 0 = norte. A VibeGame lê o canal R e mapeia `pixel/255 * max_height` para elevação em espaço mundo.
- **JSON metadata** — caminhos de rios (coordenadas pixel + mundo), posições e profundidades de lagos, planos de água para VibeGame, estatísticas de geração por etapa.

### Schema JSON (excerto)

```json
{
  "version": "1.0",
  "terrain": { "size": 2048, "world_size": 256, "max_height": 50 },
  "rivers": [{ "id": 0, "source": [1024, 512], "world_path": [[128.0, 64.0], ...] }],
  "lakes": [{ "id": 0, "center_world": [100.0, 75.0], "surface_height": 22.5 }],
  "lake_planes": [{ "lake_id": 0, "pos_x": 100.0, "pos_y": 22.5, "pos_z": 75.0, "size_x": 20.0, "size_z": 15.0 }],
  "stats": { "generation_time_seconds": 12.5, "steps": { ... } }
}
```

## Integração com VibeGame

Usa o heightmap e metadata gerados no XML mundo da VibeGame:

```html
<Terrain heightmap="/assets/terrain/heightmap.png"
         world-size="256" max-height="50"
         terrain-data-url="/assets/terrain/terrain.json"></Terrain>
```

O atributo `terrain-data-url` aciona o loader de dados JSON, que cria entidades `<Water>` para rios e lagos.

## Integração com GameAssets

O pipeline `gameassets dream` pode gerar terreno automaticamente:

```bash
gameassets dream "ilha montanhosa com rios e lagos" --terrain --terrain-prompt "ilha montanhosa"
```

Variável `TERRAINGEN_BIN` se o comando `terraingen` não estiver no `PATH`.

## Configuração

| Variável | Descrição |
|----------|-----------|
| `TERRAINGEN_BIN` | Caminho para o binário `terraingen` (se não estiver no `PATH`) |

## Estrutura

```
TerrainGen/
├── src/terraingen/
│   ├── cli.py             # CLI Click (comando generate, Rich progress)
│   ├── cli_rich.py        # Integração rich-click
│   ├── heightmap.py       # Diamond-square + suavização por gradiente
│   ├── erosion.py         # Erosão hidráulica por partículas
│   ├── rivers.py          # Acumulação de fluxo + extração de rios + escavação de vales
│   ├── lakes.py           # Preenchimento de depressões + geração de lagos
│   ├── pipeline.py        # Orquestração do pipeline completo
│   └── export.py          # Exportação PNG + JSON
├── scripts/
│   └── installer.py       # Delega para o instalador unificado gamedev-shared
└── tests/
```

## Referências de algoritmos

- **Diamond-square:** [grkvlt/landscape](https://github.com/grkvlt/landscape) (Apache 2.0, Java)
- **Erosão hidráulica:** Ivo van der Veen, ["Improved Terrain Generation Using Hydraulic Erosion"](https://medium.com/@ivo.thom.vanderveen/improved-terrain-generation-using-hydraulic-erosion-2adda8e3d99b)
- **Hidrologia:** [whitebox-tools](https://github.com/jblindsay/whitebox-tools) (MIT), acumulação de fluxo D8, preenchimento de depressões Planchon-Darboux

## Licença

- **Código:** MIT — [LICENSE](LICENSE).
- **Tabela completa:** [GameDev/README_PT.md](../README_PT.md) (secção Licenças).
