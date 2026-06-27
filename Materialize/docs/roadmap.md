# Roadmap

> **Versão 2.0 entregue em 2026-06-26.** Ver [CHANGELOG.md](../CHANGELOG.md) para detalhes e alterações incompatíveis.

## Versão 1.0 (MVP) — Entregue (2026-03-15)

**Status:** Entregue (2026-03-15)

### Features

- [x] Height map generation via multi-level blur
- [x] Normal map from height via Sobel operator
- [x] Metallic map via HSL analysis
- [x] Smoothness map (base + metallic contribution)
- [x] Edge map from normal gradient
- [x] AO map (cavity-style from height)
- [x] CLI interface (clap: input, -o, -f, -q, -v, --quiet)
- [x] wgpu compute shaders (incl. pipeline 2 inputs para smoothness)
- [x] PNG/JPG/TGA/EXR support

### Limitações Conhecidas (na altura)

- Parâmetros hardcoded (sem ajuste fino) — **resolvido em 2.0** (overrides inline + presets)
- Sem configuração via arquivo — ainda pendente (TOML, ver Futuro)
- Um formato de saída por execução — ainda assim
- Resolução limitada por GPU memory — ainda assim
- Sem alpha handling — ainda assim (entra apenas na auto-deteção)

---

## Versão 2.0 — Entregue (2026-06-26)

**Status:** Entregue. Detalhes e alterações incompatíveis em [CHANGELOG.md](../CHANGELOG.md).

### Features entregues

#### Parâmetros inline

- **Overrides por cima do preset:** `--height-contrast`, `--height-blur`, `--normal-strength`, `--metallic-scale`, `--metallic-local-variance`, `--smoothness-base`, `--smoothness-boost`, `--smoothness-roughness`, `--edge-contrast`, `--ao-depth-scale` (cada um `Option<f32>`).

  ```bash
  materialize texture.png --height-contrast 1.8 --normal-strength 2.0
  ```

#### Batch processing

- Diretório ou glob como input; `--jobs N` (paralelismo CPU; GPU serial); `--skip-existing` (resume); `--progress` (`[i/N]` por imagem).

  ```bash
  materialize ./textures/ -o ./output/ --jobs 4 --progress --skip-existing
  ```

#### Auto-deteção (`-p auto`) + `info`

- Pré-passo CPU (luminância, saturação, histograma de matiz, densidade de edges, variância de contraste local, tile MSE, alpha) → preset + confiança. Auto-tile (`tile_mse < 0.005`) e auto-scale por `edge_density`. `materialize info <imagem>` imprime o relatório sem gerar mapas.

#### Mapas seletivos + novos mapas

- `--only` / `--skip` (whitelist/blacklist); `--include-curvature` (7.º mapa, Laplaciano da height); `--roughness` (exporta `1 − smoothness`).

#### Normal flip-Y + convenções

- `--normal-format opengl|directx` controla o uniform `normal_flip_y` (F4.4).

#### Conclusão de shell + env vars

- `--generate-completions bash|zsh|fish|elvish|powershell`; `MATERIALIZE_GPU_BACKEND` (`vulkan|metal|dx12|gl|primary`) e `MATERIALIZE_LOG` finalmente honrados.

#### 12 novos presets

- `concrete`, `leather`, `marble`, `sand`, `foliage`, `plaster`, `asphalt`, `brick`, `ice`, `snow`, `lava`, `water` (+ `auto`).

#### Qualidade dos mapas

- Smoothness espacial (contraste local 5×5, F4.1); detetor metálico de dois níveis (F4.5) + local-variance damping (F2.5); edge rewrite por magnitude do gradiente (F1.1).

#### Listing + exit codes

- `--list-presets`, `--list-maps`; exit codes granulares (0–6).

---

## Versão 2.1 - Configuração Avançada

**Timeline:** 2-3 semanas após v2.0

### Config Files (TOML)

**Arquivo padrão:** `materialize.toml` no diretório do input

```toml
# materialize.toml
[global]
output_format = "exr"
output_dir = "./processed"

[height]
blur_levels = 7
max_sigma = 64.0
contrast = 1.5

[normal]
intensity = 1.0
flip_y = false

[metallic]
saturation_threshold = 0.15
luminance_threshold = 0.4

[ao]
enabled = true
ray_count = 64
max_distance = 0.5
```

**Uso:**
```bash
# Auto-detecta materialize.toml
materialize texture.png

# Especifica arquivo
materialize texture.png --config=./my-config.toml

# Override inline
materialize texture.png --config=./base.toml --height-contrast=2.0
```

### Profiles

Profiles pré-definidos para casos comuns:

```bash
# Profile para tijolos
materialize brick.png --profile=brick

# Profile para metal
materialize metal.png --profile=metal

# Profile para pele/orgânico
materialize skin.png --profile=organic

# Lista profiles
materialize --list-profiles
```

**Built-in profiles:**
- `default`: Configurações equilibradas
- `brick`: Blur mais forte para padrões de tijolo
- `metal`: Thresholds ajustados para metais
- `organic`: Suavização extra para superfícies naturais
- `tile`: Configurado para texturas tileáveis

---

## Versão 3.0 - Preview Window

**Timeline:** 1-2 meses após v2.1

### Feature Principal: Preview 3D

**Descrição:** Janela SDL2/GLFW para preview rápido do material PBR completo

**Comando:**
```bash
materialize texture.png --preview

# Preview apenas após processamento
materialize texture.png && materialize texture.png --preview-only
```

**Controles:**
- `Left click + drag`: Rotacionar
- `Right click + drag`: Pan
- `Scroll`: Zoom
- `H/N/M/S`: Toggle mapas (Height/Normal/Metallic/Smoothness)
- `Space`: Toggle wireframe
- `Esc`: Fechar

### Shader de Preview

Pipeline de preview simples:
```
Diffuse + Normal + Metallic + Smoothness + AO → PBR shading
```

**Iluminação:**
- 3 point lights (key, fill, rim)
- Cubemap environment (opcional)
- Rotation automática

---

## Versão 3.1 - Seamless/Tiling

**Timeline:** 1 mês após v3.0

### Features

#### Seamless Texture Maker

Converte textura não-tileável em tileável:

```bash
materialize texture.png --make-seamless --output=seamless.png
```

**Algoritmo:**
1. Wrap edges com blending
2. Frequency analysis para patterns
3. Poisson blending nas junções

#### Tiling Preview

```bash
materialize texture.png --preview --tiling=2x2  # Mostra 2x2 tiles
materialize texture.png --preview --tiling=4x4  # Mostra 4x4 tiles
```

#### Seamless Maps

Todos os mapas gerados são automaticamente seamless se input for:
- Height: Wrapping com derivadas consistentes
- Normal: Wrapping com continuidade
- Metallic: Simples wrap (não afeta vizinhança)

---

## Versão 4.0 - Advanced Algorithms

**Timeline:** 2-3 meses após v3.1

### Machine Learning

#### ML-Based Metallic Detection

- Modelo treinado em dataset de materiais PBR
- Melhor detecção que heurísticas HSL
- Suporte para metais pintados/oxidados

```bash
materialize texture.png --metallic-ml --model=./my-model.onnx
```

#### Super-Resolution

Upscale + geração de mapas simultâneo:

```bash
materialize lowres.png --upscale=2x  # 1K → 2K
materialize lowres.png --upscale=4x  # 1K → 4K
```

### Advanced Height

#### Machine Learning Height

Extrai height com ML (melhor que luminância):

```bash
materialize texture.png --height-ml
```

#### Depth-from-Defocus (DfD)

Se múltiplas imagens com diferentes focos disponíveis:

```bash
materialize --dfd ./focus_stack/ --output=height.png
```

---

## Versão 5.0 - Plugin System

**Timeline:** 3-6 meses após v4.0

### Plugin Architecture

Plugins em Rust (dynamic libs) ou Lua/Python scripts:

```bash
# Carregar plugin
materialize texture.png --plugin=./my-plugin.so

# Plugins podem:
# - Adicionar novos mapas
# - Modificar pipeline existente
# - Adicionar novos algoritmos
```

### Marketplace de Plugins

```bash
# Instalar plugin do registry
materialize plugin install normal-enhancer

# Listar plugins
materialize plugin list

# Desenvolver plugin
materialize plugin new my-plugin  # Gera template
```

---

## Versões Futuras (Sem Timeline)

### Features Consideradas

- [ ] **CLI Server Mode:** `materialize serve` para processamento via API HTTP
- [ ] **Watch Mode:** `materialize --watch ./textures/` - re-processa em mudanças
- [ ] **GUI Mode:** Interface gráfica opcional (egui/iced)
- [ ] **Cloud Processing:** Offload para GPUs cloud
- [ ] **Batch Config:** Processar múltiplas configs em uma execução
- [ ] **Image Sequence:** Processar vídeos/texturas animadas
- [ ] **Normal Map Combine:** Combinar múltiplas normais (detail mapping)
- [ ] **Curvature-Driven:** Ajustar parâmetros baseado em curvatura local
- [ ] **AO por ray marching:** AO com normal+height como no Materialize original (ray count, spread, depth); substituir o cavity-style atual
- [ ] **Tiled processing:** Processar imagens maiores que a VRAM em tiles com costura

---

## Prioridades

### Prioridade Alta (Must Have)

1. MVP funcional e estável
2. Batch processing (essencial para pipelines)
3. Config files (usabilidade)

### Prioridade Média (Should Have)

4. AO (diferencial do Materialize original)
5. Preview window (UX)
6. Smoothness (completa o set PBR básico)

### Prioridade Baixa (Nice to Have)

7. Seamless maker
8. ML features
9. Plugin system
10. GUI mode

---

## Contribuições

Features da comunidade são bem-vindas! Abra uma issue para discutir:

- Novos mapas/algoritmos
- Integrações com engines
- Performance improvements
- Bug fixes

**Label `good-first-issue`:** Issues ideais para novos contribuidores
