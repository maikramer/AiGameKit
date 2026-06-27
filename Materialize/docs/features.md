# Funcionalidades

## Geração de Mapas PBR

### Height Map

Converte uma imagem colorida em um mapa de altura em escala de cinza.

**Entrada:** Imagem difusa (RGBA)
**Saída:** Height map (grayscale, R32Float internamente)

**Algoritmo:**
1. Converter para luminância (grayscale)
2. Aplicar Gaussian blur em múltiplos níveis
3. Combinar com pesos ajustáveis
4. Aplicar contraste e spread

**Uso típico:**
- Displacement mapping
- Parallax occlusion mapping
- Base para normal map generation

### Normal Map

Gera vetores de superfície a partir do height map.

**Entrada:** Height map (gerado internamente)
**Saída:** Normal map (RGB, formato DirectX/OpenGL)

**Algoritmo:**
1. Calcular gradientes via operador Sobel
2. Construir vetores normais (x, y, z)
3. Normalizar e codificar para [0, 255]

**Formato de saída:**
- Red channel: X component (-1 to +1) → 0 to 255
- Green channel: Y component (-1 to +1) → 0 to 255  
- Blue channel: Z component (0 to +1) → 128 to 255

**Uso típico:**
- Iluminação em tempo real
- Bump mapping
- Detalhes de superfície sem geometria adicional

### Metallic Map

Detecta áreas metálicas por análise de cor usando um detetor de dois níveis.

**Entrada:** Imagem difusa original (RGBA)
**Saída:** Metallic map (grayscale, 0 = dieletric, 1 = metal)

**Algoritmo:** Converter RGB para HSL e aplicar um detetor de dois níveis (F4.5):

1. **Grupo acromático** (saturação < 0.15): cobre aço, prata, alumínio, titânio, pewter e chrome, com um *bonus* opcional para tons azulados (blue steel). Score por luminância × (1 − saturação).
2. **Quatro bandas cromáticas** de matiz não sobrepostas:
   - **Cobre** — hue 0.00–0.06
   - **Bronze** — hue 0.06–0.09
   - **Ouro** — hue 0.09–0.14
   - **Latão** — hue 0.14–0.17

Cada banda tem score baseado em luminância e saturação. As bandas não se sobrepõem, ao contrário do detetor antigo (onde bronze/latão eram subconjuntos de cobre/ouro).

**Local-variance damping (F2.5):** metais puros têm baixa variância local de luminância; texturas não-metálicas (betão, pedra cinzenta) têm variância alta. O uniform `metallic_local_variance_factor` (0..1, configurável via `--metallic-local-variance`) amortiza a deteção em regiões texturizadas, reduzindo falsos positivos.

**Uso típico:**
- Physically based rendering (PBR)
- Diferenciação metal/não-metal
- Reflexos específicos por material

### Smoothness Map

Define rugosidade/suavidade da superfície para PBR.

**Entrada:** Imagem difusa + mapa metallic (gerados internamente)
**Saída:** Smoothness map (grayscale, 0 = rugoso, 1 = liso)

**Algoritmo:** A smoothness é agora espacial (F4.1) e combina três termos:

```
smoothness = base + metallic_boost * metallic - roughness_factor * local_contrast_5x5
```

Regiões texturizadas (alto contraste local numa janela 5×5) produzem menos smoothness; regiões planas produzem mais. O comportamento do MVP fica preservado quando `smoothness_roughness_factor == 0`. Metais continuam a tender para lisos (via o termo `metallic_boost`). Com `--roughness`, o CLI exporta `1 - smoothness` como mapa de roughness.

**Uso típico:** Roughness/smoothness em shaders PBR (Unity, Unreal, etc.).

### Edge Map

Destaca bordas e vincos a partir do mapa de normal.

**Entrada:** Normal map (gerado internamente)
**Saída:** Edge map (grayscale)

**Algoritmo:** Usa a magnitude do gradiente da normal (amostras ±1 pixel em X e Y) com um limiar *smoothstep* (F1.1). O shader antigo calculava `(diff_x + 0.5) * (diff_y + 0.5) * 2.0` com gradientes centrados em 0, o que produzia uma saída quase plana (~0.5) em todo o lado; agora a magnitude real do gradiente é limiarizada de forma suave. Inspirado no Materialize original (Blit_Edge_From_Normal).

**Uso típico:** Outline, cavity, ou máscaras para pós-processamento.

### AO Map (Ambient Occlusion)

Oclusão ambiente no estilo cavity, a partir do height map.

**Entrada:** Height map (gerado internamente)
**Saída:** AO map (grayscale, 0 = ocluído, 1 = aberto)

**Algoritmo:** Amostras em 8 direções (raios 1 e 2 pixels); oclusão quando altura da amostra > centro; resultado invertido e escalado.

**Uso típico:** Sombreamento em frestas e cantos em pipelines PBR.

### Curvature Map _(opt-in)_

Curvatura convexa/côncava da superfície, derivada da height via Laplaciano.

**Entrada:** Height map (gerado internamente)
**Saída:** Curvature map (grayscale, 0.5 = plano, >0.5 = côncavo, <0.5 = convexo)

**Algoritmo:** Laplaciano da height numa vizinhança 3×3, normalizado para [0,1] à volta de 0.5. É o sétimo mapa e é **opt-in**: só é gerado com `--include-curvature`. Pode ser combinado com `--only curvature,...` / `--skip`.

**Uso típico:** Máscaras de desgaste (edge wear), oclusão de curvatura, guiar blend de materiais ou pintura procedural.

## Auto-deteção (`-p auto`)

Em vez de escolher um preset manualmente, `-p auto` faz um pré-passo rápido na CPU sobre a textura e extrai um vetor de features:

- Média / desvio-padrão de **luminância**
- Média / desvio-padrão de **saturação**
- **Histograma de matiz** (12 bins)
- **Densidade de edges** Sobel
- **Variância de contraste local** (5×5)
- **Tile MSE** (linhas/colunas de borda topo/fundo + esquerda/direita completas)
- **Cobertura alpha**

Uma árvore de decisão mapeia estas features para um dos presets (metal, skin, wood, stone, foliage, floor, default) e imprime uma **pontuação de confiança**. Aplica ainda:

- **Auto-tile:** quando `tile_mse < 0.005`, todos os shaders de amostragem de vizinhança (height blur, Sobel, edge gradient, AO cavity, curvature Laplacian) mudam de clamp para wrap (módulo euclidiano) nas bordas, para os mapas ficarem tileáveis. Override manual via `--seamless` / `--no-seamless`.
- **Auto-scale:** `height_contrast` e `normal_strength` são ajustados pela `edge_density` (texturas detalhadas ganham mais contraste; texturas muito ruidosas ficam com normais suavizadas).

Use `materialize info <imagem>` para pré-visualizar a análise completa sem gerar mapas.

## Formatos Suportados

### Entrada (Leitura)

| Formato | Extensões | Observações |
|---------|-----------|-------------|
| PNG | .png | Recomendado, lossless |
| JPEG | .jpg, .jpeg | Lossy, bom para fotos |
| TGA | .tga | Legacy, games |
| BMP | .bmp | Limitado, suportado |
| EXR | .exr | HDR, linear |

### Saída (Escrita)

| Formato | Extensões | Melhor uso |
|---------|-----------|------------|
| PNG | .png | Geral (lossless) |
| JPEG | .jpg | Compacto (lossy) |
| TGA | .tga | Games engines |
| EXR | .exr | Normal maps (float precision) |

**Configurações de qualidade:**
- PNG: Compression level 6 (padrão)
- JPEG: Qualidade 95% (padrão), configurável 0-100
- EXR: P-tiles, zip compression

## Interface CLI

### Comando Básico

```bash
materialize <INPUT> [OPTIONS]
```

### Opções

| Opção | Curta | Descrição | Padrão |
|-------|-------|-----------|--------|
| `--output` | `-o` | Diretório de saída | `.` |
| `--format` | `-f` | Formato de saída (png, jpg, tga, exr) | png |
| `--preset` | `-p` | Preset de material (ver presets; `auto` deteta) | default |
| `--quality` | `-q` | Qualidade JPEG (1-100); `0` clampado a `1` | 95 |
| `--verbose` | `-v` | Progresso, tempos por estágio e auto-detect | false |
| `--quiet` | | Suprimir lista de arquivos gerados | false |
| `--include-curvature` | | Gerar `texture_curvature.png` (7.º mapa) | false |
| `--roughness` | | Exportar roughness (`1 - smoothness`) em vez de smoothness | false |
| `--normal-format` | | `opengl` (Y-up) ou `directx` (Y-down) | opengl |
| `--only` | | Whitelist de mapas | — |
| `--skip` | | Blacklist de mapas (exclui `--only`) | — |
| `--seamless` / `--no-seamless` | | Forçar wrap/clamp nas bordas | auto |
| `--jobs` | | Paralelismo CPU no batch (GPU serial) | 1 |
| `--skip-existing` | | Retomar (saltar com height já existente) | false |
| `--progress` | | Mostrar `[i/N]` por imagem no batch | false |
| `--list-presets` | | Listar presets e sair | — |
| `--list-maps` | | Listar mapas gerados e sair | — |
| `--generate-completions` | | Conclusão de shell (bash/zsh/fish/elvish/powershell) | — |
| `--help` | `-h` | Mostrar ajuda | - |
| `--version` | `-V` | Mostrar versão | - |

**Overrides inline** (por cima do preset): `--height-contrast`, `--height-blur`,
`--normal-strength`, `--metallic-scale`, `--metallic-local-variance`,
`--smoothness-base`, `--smoothness-boost`, `--smoothness-roughness`,
`--edge-contrast`, `--ao-depth-scale`.

### Exemplos de Uso

```bash
# Básico - gera na mesma pasta
materialize texture.png
# Resultado: texture_height.png, texture_normal.png, texture_metallic.png,
#           texture_smoothness.png, texture_edge.png, texture_ao.png

# Diretório de saída específico
materialize texture.png -o ./materials/

# Formato diferente
materialize texture.png -f exr

# JPEG com qualidade baixa (mais compacto)
materialize texture.jpg -f jpg -q 80

# Verbose - mostra progresso; --quiet suprime a lista de arquivos
materialize texture.png -v
materialize texture.png --quiet

# Instalar a skill do Cursor no projeto atual
materialize skill install
```

## Funcionalidades Futuras (Roadmap)

O roadmap completo está em [docs/roadmap.md](roadmap.md). A versão 2.0 já entregou:

- **Batch processing** — diretório/glob como input, `--jobs N`, `--skip-existing`, `--progress`
- **Parâmetros inline** — overrides por cima do preset (`--height-contrast`, `--normal-strength`, etc.)
- **Auto-deteção** — `-p auto` + `materialize info`
- **Mapas seletivos** — `--only` / `--skip`
- **Curvature map** — opt-in via `--include-curvature`
- **Roughness output** — `--roughness`
- **Normais OpenGL/DirectX** — `--normal-format`
- **Conclusão de shell** — `--generate-completions`
- **Variáveis de ambiente** — `MATERIALIZE_GPU_BACKEND`, `MATERIALIZE_LOG`
- **12 novos presets** (concrete, leather, marble, sand, foliage, plaster, asphalt, brick, ice, snow, lava, water)

Ainda no futuro: AO por ray marching, deteção metálica por ML, super-resolution, sistema de plugins, GUI, processamento em tiles e configuração TOML.

## Casos de Uso

### Game Development

```bash
# Pipeline de assets
for texture in assets/textures/raw/*.png; do
    materialize "$texture" -o assets/textures/processed/
done
```

### 3D Art / Blender

```bash
# Preparar textura para importação
materialize photo.jpg -f exr -o ~/blender_project/textures/
# Importar height para displacement, normal para bump, metallic para shader
```

### Web Development (Three.js/Babylon)

```bash
# Otimizar para web (JPEG compacto)
materialize texture.png -f jpg -q 85 -o ./public/textures/
```

### Archviz

```bash
# Materiais de alta qualidade
materialize marble_scan.png -f exr -o ./materials/marble/
# EXR preserva precisão de cor para materiais PBR
```

## Limitações Conhecidas

### Versão 2.0

1. **Resolução máxima:** Limitada por memória GPU (tipicamente 8K+). Imagens que excedem a VRAM devolvem exit code `6`.
2. **Um formato por execução:** Todos os mapas saem no mesmo formato (`-f`); não há formato por mapa individual.
3. **Sem alpha handling:** O canal alpha ainda é ignorado no processamento (mas entra na análise de auto-deteção como cobertura alpha).
4. **AO simplificado:** Ainda é cavity-style a partir da height; o AO por ray marching (normal+height) do Materialize original continua como trabalho futuro.
5. **Paralelismo GPU:** `--jobs` só paraleliza a fase CPU (load/analyse); o despacho GPU é serializado.

> Já resolvido em 2.0: parâmetros agora são sobreponíveis inline e por preset (antes eram hardcoded); exit codes granulares; conclusão de shell; variáveis de ambiente; auto-deteção e mapas seletivos.

### Futuro

As restantes limitações serão endereçadas conforme roadmap (AO ray-march, tiled processing, configuração TOML, etc.).
