# Materialize CLI

*Versão em português. [English README](README.md).*

Uma **CLI em Rust** que gera mapas PBR (renderização fisicamente baseada) a partir de texturas difusas/albedo usando *compute shaders* na GPU via [wgpu](https://wgpu.rs/). Sem interface gráfica, sem Unity — um comando e seis (ou sete) mapas.

Inspirada no [Materialize](https://github.com/BoundingBoxSoftware/Materialize) original da Bounding Box Software (Unity/Windows). Esta é uma reimplementação do zero em Rust que preserva o mesmo conceito — transformar uma única imagem difusa num conjunto completo de mapas PBR.

**Monorepo GameDev:** o **Hunyuan3D-Paint 2.1** (`paint3d texture`) já produz **GLB PBR**. Esta CLI serve para **mapas PBR a partir de uma imagem difusa** (ex.: **Texture2D** + `texture2d.materialize` no GameAssets). Ver [`Text3D/docs/PBR_MATERIALIZE.md`](../Text3D/docs/PBR_MATERIALIZE.md).

---

## Visão geral

A partir de uma única textura difusa/albedo, o Materialize produz até sete mapas PBR:

| Mapa | Descrição |
|------|-----------|
| **Height** | Elevação da superfície para parallax/deslocamento |
| **Normal** | Normais da superfície para iluminação realista |
| **Metallic** | Máscara metálico vs dielétrico |
| **Smoothness** | Rugosidade inversa (base + metallic + contribuição de contraste local) |
| **Edge** | Deteção de arestas derivada das normais |
| **AO** | Oclusão ambiente (estilo cavidade, derivada da height) |
| **Curvature** _(opt-in)_ | Curvatura convexa/côncava (Laplaciano da height) — ative com `--include-curvature` |

**Propriedades principais:**

- **Rápida** — *Compute shaders* na GPU via wgpu; sem loops pesados na CPU
- **Multiplataforma** — Linux, macOS, Windows (Vulkan, Metal, DirectX 12)
- **Sem CUDA** — wgpu funciona com qualquer GPU moderna
- **Auto-detect** — `-p auto` analisa a textura e escolhe o melhor preset
- **Batch-friendly** — diretórios e globs, `--skip-existing` para retomar
- **Scriptável** — Só CLI; códigos de saída estáveis

---

## Instalação

### Monorepo GameDev (recomendado)

A partir da raiz do repositório:

```bash
./install.sh materialize
```

Compila o crate e instala o binário em `~/.local/bin/`. Confirme que `~/.local/bin` está no seu `PATH`.

### Compilação standalone

Requer **Rust** 1.87+ e uma GPU com controladores atualizados.

```bash
cd Materialize
cargo build --release
cargo install --path .
```

O binário `materialize` fica em `~/.cargo/bin/`.

### Script de instalação manual

```bash
git clone https://github.com/maikramer/Materialize-CLI.git
cd Materialize-CLI
./install.sh          # instalar
./install.sh reinstall
./install.sh uninstall
```

---

## Comandos

### `materialize <INPUT>` (comando por omissão)

Gera mapas PBR a partir de uma imagem difusa/albedo. `INPUT` pode ser um ficheiro, um diretório ou um padrão glob.

```bash
materialize texture.png
materialize texture.png -o ./out/ -v
materialize skin.png --preset skin -o ./materials/
materialize ./textures/ -o ./pbr/ --jobs 4 --progress
materialize texture.png -p auto -v
```

#### Opções

| Flag | Curta | Tipo | Padrão | Descrição |
|------|-------|------|--------|-----------|
| `--output` | `-o` | path | `.` | Diretório de saída |
| `--format` | `-f` | enum | `png` | Formato de saída: `png`, `jpg`, `tga`, `exr` |
| `--preset` | `-p` | enum | `default` | Preset de material (ver abaixo) |
| `--quality` | `-q` | int | `95` | Qualidade JPEG 1–100 (ignorada noutros formatos) |
| `--verbose` | `-v` | flag | — | Mostrar progresso, tempos e info de auto-detect |
| `--quiet` | — | flag | — | Suprimir a lista de ficheiros gerados em caso de sucesso |
| `--include-curvature` | — | flag | — | Gerar `texture_curvature.png` (7.º mapa) |
| `--roughness` | — | flag | — | Gerar `texture_roughness.png` (= `1 - smoothness`) em vez de `texture_smoothness.png` |
| `--normal-format` | — | enum | `opengl` | Convenção do eixo Y da normal: `opengl` (Y-up) ou `directx` (Y-down) |
| `--only` | — | lista | — | Whitelist de mapas: `height,normal,metallic,smoothness,edge,ao,curvature` |
| `--skip` | — | lista | — | Blacklist de mapas (mutuamente exclusivo com `--only`) |
| `--seamless` / `--no-seamless` | — | flag | auto | Forçar wrap ou clamp na amostragem das bordas |
| `--jobs` | — | int | `1` | Paralelismo CPU no batch (GPU fica serial) |
| `--skip-existing` | — | flag | — | Saltar imagens cujo height já existe |
| `--progress` | — | flag | — | Mostrar `[i/N]` por imagem no batch |
| `--list-presets` | — | flag | — | Listar todos os presets e sair |
| `--list-maps` | — | flag | — | Listar todos os nomes de mapas gerados e sair |
| `--generate-completions` | — | enum | — | Gerar conclusão de shell: `bash`, `zsh`, `fish`, `elvish`, `powershell` |
| `--help` | `-h` | — | — | Mostrar ajuda |
| `--version` | `-V` | — | — | Mostrar versão |

#### Overrides inline (aplicados por cima do preset)

`--height-contrast`, `--height-blur`, `--normal-strength`, `--metallic-scale`,
`--metallic-local-variance`, `--smoothness-base`, `--smoothness-boost`,
`--smoothness-roughness`, `--edge-contrast`, `--ao-depth-scale`.

### `materialize info <imagem>`

Analisa uma textura sem gerar mapas. Imprime o preset detetado, a pontuação de confiança e o vetor de features completo.

```bash
materialize info texture.png
```

### `materialize skill install`

Instala a [skill do Cursor](.cursor/skills/materialize-cli/) do Materialize CLI no projeto atual, em `.cursor/skills/materialize-cli/`.

```bash
materialize skill install
```

---

## Presets PBR

19 presets de material mais `auto`. Use `-p` / `--preset` para escolher.

| Preset | Descrição | Características |
|--------|-----------|-----------------|
| `default` | Uso geral | Configurações equilibradas para qualquer textura |
| `skin` | Pele humana/personagem | Sem metallic, smoothness alta, normais subtis |
| `floor` | Solos (pedra, azulejo, terra) | Height pronunciado, AO forte, superfície áspera |
| `metal` | Superfícies metálicas | Metallic reforçado, edges nítidos, aspeto polido |
| `fabric` | Tecido/têxtil | Matte, sem metallic, edges suaves |
| `wood` | Madeira | Sem metallic, detalhe de grão moderado |
| `stone` | Pedra/rocha | Muito áspero, AO profundo, normais fortes |
| `concrete` | Betão | Áspero, cinzento, ruído denso de superfície |
| `leather` | Couro | Granulado, semi-liso, tons quentes |
| `marble` | Mármore | Polido, veios, liso |
| `sand` | Areia | Grão fino, muito áspero |
| `foliage` | Folhas/erva | Orgânico, metallic baixo, detalhe médio |
| `plaster` | Reboco/stucco | Plano, normais suaves |
| `asphalt` | Asfalto | Escuro, áspero, edges densos |
| `brick` | Tijolo | Edges nítidos, superfície áspera |
| `ice` | Gelo | Muito liso, detalhe ligeiro |
| `snow` | Neve | Suave, difusa |
| `lava` | Lava | Fundida, semi-metálica |
| `water` | Água | Muito lisa, fluída |
| `auto` | Auto-detect | Analisar features da textura e escolher o melhor preset |

```bash
materialize brick_diffuse.png -p stone -o ./out/
materialize character_skin.png --preset skin -v
materialize metal_panel.jpg -p metal -f exr -o ./hdr/
materialize mystery.png -p auto -v
```

---

## Auto-deteção (`-p auto`)

`-p auto` faz um pré-passo rápido na CPU sobre a textura e calcula:

- Média/desvio-padrão de luminância
- Média/desvio-padrão de saturação
- Histograma de matiz (12 bins)
- Densidade de edges Sobel
- Variância de contraste local 5×5
- Tile MSE (linhas/colunas de borda topo/fundo + esquerda/direita completas)
- Cobertura alpha

Uma árvore de decisão mapeia estas features para um preset (metal, skin, wood, stone, foliage, floor, default) mais uma pontuação de confiança. Quando o tile MSE é inferior a `0.005`, o pipeline troca todos os shaders de amostragem de vizinhança para wrap (módulo euclidiano) nas bordas, para os mapas gerados ficarem tileáveis.

Use `materialize info <imagem>` para pré-visualizar a análise sem gerar mapas.

---

## Variáveis de ambiente

| Variável | Valores | Descrição |
|----------|---------|-----------|
| `MATERIALIZE_GPU_BACKEND` | `vulkan` · `metal` · `dx12` · `gl` · `primary` | Forçar um backend wgpu específico (padrão: `primary`) |
| `MATERIALIZE_LOG` | `error` · `warn` · `info` · `debug` · `trace` | Nível de log (padrão: `warn`) |

```bash
MATERIALIZE_GPU_BACKEND=vulkan materialize texture.png
MATERIALIZE_LOG=debug materialize texture.png -v
```

---

## Ficheiros de saída

A partir de um input `texture.png`, o Materialize gera até sete ficheiros no diretório de saída (a extensão segue `--format`):

| Ficheiro | Descrição |
|----------|-----------|
| `texture_height.{ext}` | Mapa de height/deslocamento |
| `texture_normal.{ext}` | Mapa de normais |
| `texture_metallic.{ext}` | Mapa de metallic |
| `texture_smoothness.{ext}` ou `texture_roughness.{ext}` | Smoothness (padrão) ou roughness (`--roughness`) |
| `texture_edge.{ext}` | Mapa de deteção de edges |
| `texture_ao.{ext}` | Mapa de oclusão ambiente |
| `texture_curvature.{ext}` | Mapa de curvatura (só com `--include-curvature`) |

### Códigos de saída

| Código | Significado |
|--------|-------------|
| `0` | Sucesso — ficheiros gerados |
| `1` | Erro genérico |
| `2` | Ficheiro de entrada não encontrado |
| `3` | Formato de entrada não suportado |
| `4` | Erro de GPU (sem *adapter*, falha ao criar *device*, …) |
| `5` | Erro de I/O (permissões, disco cheio, …) |
| `6` | Imagem demasiado grande para a GPU |

---

## Integração em pipeline

O Materialize corre **depois** do Texture2D ou Paint3D para gerar mapas PBR a partir de texturas difusas. Está integrado no pipeline do monorepo GameDev em vários pontos:

- **GameAssets batch** — via `materialize: true` no bloco `texture2d` do `game.yaml`
- **Paint3D** — o comando `vertex-pbr` usa o Materialize para gerar mapas
- **Standalone** — processa qualquer imagem difusa de qualquer origem

Usa compute shaders wgpu para aceleração GPU multiplataforma — sem CUDA. Funciona em Linux (Vulkan), macOS (Metal) e Windows (DirectX 12) com qualquer GPU moderna.

```bash
# Pipeline típico: gerar textura, depois mapas PBR
texture2d generate "brick wall" -o ./textures/
materialize textures/brick_wall.png -p auto -o ./materials/brick/
```

### Processamento em batch

```bash
# Processar um diretório (serial; --jobs controla o paralelismo CPU)
materialize ./textures/ -o ./pbr/ --jobs 4 --progress

# Retomar após interrupção
materialize ./textures/ -o ./pbr/ --skip-existing

# Padrão glob
materialize "./textures/bricks/*.png" -o ./pbr/

# Adequado a scripts: modo silencioso, verificar código de saída
materialize texture.png -o ./out/ --quiet
if [ $? -eq 0 ]; then echo "Mapas PBR gerados"; fi
```

---

## Exemplos

```bash
# Predefinição: diretório atual, PNG
materialize brick.png

# Diretório de saída e modo verboso
materialize brick.png -o ./materials/brick/ -v

# EXR para HDR / precisão
materialize texture.png -f exr -o ./out/

# Só height + normal
materialize texture.png --only height,normal -o ./out/

# Curvature + roughness
materialize texture.png --include-curvature --roughness -o ./out/

# Normais DirectX
materialize texture.png --normal-format directx -o ./out/

# Lote com resume
materialize ./textures/ -o ./pbr/ --jobs 4 --skip-existing --progress
```

---

## Desenvolvimento

```bash
cd Materialize

# Build
cargo build

# Testes
cargo test

# Formatar (auto-fix)
cargo fmt

# Lint
cargo clippy -- -D warnings
```

Requer **Rust** 1.87+ (edition 2024). Dependência de dev: `tempfile` para testes de integração.

---

## Obter ajuda

- **Bugs e funcionalidades** — [Abrir um *issue*](https://github.com/maikramer/Materialize-CLI/issues) (há modelos para relatórios de bug e pedidos de funcionalidade).
- **Dúvidas** — [GitHub Discussions](https://github.com/maikramer/Materialize-CLI/discussions).
- **Contribuir** — Ver [CONTRIBUTING.md](CONTRIBUTING.md). Seguimos um [Código de conduta](CODE_OF_CONDUCT.md).

---

## Documentação

- [README.md](README.md) — Visão geral (inglês)
- [docs/README.md](docs/README.md) — Visão geral, detalhes de instalação e índice da documentação
- [docs/cli-api.md](docs/cli-api.md) — Referência completa da CLI, variáveis de ambiente, conclusão na shell
- [docs/architecture.md](docs/architecture.md) — Estrutura do sistema
- [docs/features.md](docs/features.md) — Capacidades
- [docs/algorithms.md](docs/algorithms.md) — Algoritmos de processamento
- [docs/shaders.md](docs/shaders.md) — *Shaders* WGSL
- [docs/roadmap.md](docs/roadmap.md) — Planos futuros
- [CHANGELOG.md](CHANGELOG.md) — Histórico de releases (alterações incompatíveis do 2.0)

---

## Licença

**Licença MIT** — ver [LICENSE](LICENSE).

Este projeto baseia-se no [Materialize](https://github.com/BoundingBoxSoftware/Materialize) da **Bounding Box Software**. O Materialize original é uma aplicação Unity/Windows para gerar mapas PBR. Esta é uma reimplementação do zero em Rust que preserva o conceito e a abordagem algorítmica.
