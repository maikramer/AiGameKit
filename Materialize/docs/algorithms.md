# Algoritmos de Processamento

## Visão Geral

O Materialize CLI implementa sete algoritmos principais, cada um executado como compute shader WGSL, mais um pré-passo de análise na CPU para auto-deteção:

1. **Height from Diffuse** - Extrai informação de altura da imagem colorida
2. **Normal from Height** - Calcula vetores normais a partir do height map
3. **Metallic from Diffuse** - Detecta metalicidade por análise de cor (detetor de dois níveis)
4. **Smoothness** - base + metallic_boost × metallic − roughness_factor × contraste local 5×5 (difusa + metallic como entrada)
5. **Edge from Normal** - Magnitude do gradiente da normal com limiar smoothstep
6. **AO from Height** - Cavity-style: amostras em 8 direções, oclusão quando vizinho > centro
7. **Curvature from Height** _(opt-in)_ - Laplaciano da height (0.5 = plano)

Todos os shaders de amostragem de vizinhança partilham um helper `sample_coord` que alterna entre clamp e wrap consoante o uniform `params.seamless` (F2.4).

## 1. Height Map Generation

### Objetivo

Converter uma imagem RGB em um mapa de altura em escala de cinza onde:
- Branco (1.0) = pontos altos
- Preto (0.0) = pontos baixos
- Valores intermediários = gradiente de altura

### Algoritmo Completo

#### Passo 1: Luminance Conversion

Converte RGB para luminância usando pesos perceptuais:

```wgsl
fn rgb_to_luminance(rgb: vec3<f32>) -> f32 {
    return dot(rgb, vec3<f32>(0.299, 0.587, 0.114));
}
```

**Rationale:** Pesos baseados na sensibilidade do olho humano (verde mais perceptível).

#### Passo 2: Multi-Level Gaussian Blur

Aplica Gaussian blur em múltiplas escalas para capturar detalhes em diferentes frequências:

```
Level 0: σ = 1.0  (detalhes finos)
Level 1: σ = 2.0  (detalhes médios)
Level 2: σ = 4.0  (formas grandes)
Level 3: σ = 8.0  (estrutura geral)
Level 4: σ = 16.0 (contornos grandes)
Level 5: σ = 32.0 (formas principais)
Level 6: σ = 64.0 (estrutura macro)
```

**Pesos de combinação:**
```
weights = [0.5, 0.3, 0.15, 0.03, 0.015, 0.003, 0.002]
```

**Kernel Gaussiano 1D (para separação):**
```
G(x) = (1.0 / (sqrt(2π) * σ)) * exp(-x² / (2σ²))
```

**Implementação separável:**
- Passo horizontal: amostra linha, aplica kernel 1D
- Passo vertical: amostra coluna, aplica kernel 1D
- Mais eficiente que kernel 2D (O(n) vs O(n²))

#### Passo 3: Contrast Enhancement

Aplica sigmoid para aumentar contraste:

```wgsl
fn enhance_contrast(value: f32, contrast: f32) -> f32 {
    // Map [0,1] to [-1,1]
    let centered = value * 2.0 - 1.0;
    // Sigmoid function
    let enhanced = centered / (1.0 + exp(-contrast * centered));
    // Map back to [0,1]
    return (enhanced + 1.0) * 0.5;
}
```

**Parâmetros:**
- `contrast = 1.0`: Linear
- `contrast > 1.0`: Mais contraste
- `contrast < 1.0`: Menos contraste

### Pseudocódigo WGSL

```wgsl
@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let coords = vec2<i32>(gid.xy);
    let dims = textureDimensions(input_texture);
    
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    
    // Sample and convert to luminance
    let color = textureLoad(input_texture, coords, 0).rgb;
    let luminance = rgb_to_luminance(color);
    
    // Multi-level blur
    var height = 0.0;
    let weights = array<f32, 7>(0.5, 0.3, 0.15, 0.03, 0.015, 0.003, 0.002);
    
    for (var level = 0; level < 7; level++) {
        let sigma = f32(1 << level);  // 1, 2, 4, 8, 16, 32, 64
        let blurred = gaussian_blur(luminance, coords, sigma, dims);
        height += weights[level] * blurred;
    }
    
    // Contrast enhancement
    height = enhance_contrast(height, 1.5);
    
    textureStore(output_texture, coords, vec4<f32>(height, 0.0, 0.0, 1.0));
}
```

#### Amostragem `sample_coord` (F2.4)

Na realidade, o shader `height.wgsl` não usa `gaussian_blur`/`safe_sample` separados; toda a amostragem de vizinhança passa por um helper `sample_coord` que alterna entre clamp e wrap consoante o uniform `params.seamless`:

```wgsl
fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (params.seamless == 1u) {
        return ((coords % d) + d) % d;              // wrap (tileável)
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));  // clamp
}
```

Quando `-p auto` deteta `tile_mse < 0.005` (ou com `--seamless`), `seamless = 1` e os mapas ficam tileáveis nas bordas.

### Parâmetros (MVP Defaults)

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| Blur levels | 7 | Número de níveis de blur |
| Max sigma | 64.0 | Blur mais amplo |
| Contrast | 1.5 | Fator de contraste |

## 2. Normal Map Generation

### Objetivo

Calcular vetores normais da superfície a partir do gradiente do height map.

### Teoria

Uma normal é perpendicular à superfície. Calculamos via:

```
normal = normalize(cross(dY, dX))
```

Onde:
- `dX` = vetor tangente na direção X (1, 0, ∂height/∂x)
- `dY` = vetor tangente na direção Y (0, 1, ∂height/∂y)

### Operador Sobel

Calcula gradientes usando kernels 3x3:

**Sobel X (horizontal):**
```
[-1, 0, +1]
[-2, 0, +2]
[-1, 0, +1]
```

**Sobel Y (vertical):**
```
[-1, -2, -1]
[ 0,  0,  0]
[+1, +2, +1]
```

### Pseudocódigo WGSL

```wgsl
fn sample_height(coords: vec2<i32>) -> f32 {
    return textureLoad(height_texture, coords, 0).r;
}

@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let coords = vec2<i32>(gid.xy);
    let dims = textureDimensions(height_texture);
    
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    
    // Sobel operator
    let gx = sample_height(coords + vec2<i32>(-1, -1)) * -1.0
           + sample_height(coords + vec2<i32>(-1,  0)) * -2.0
           + sample_height(coords + vec2<i32>(-1,  1)) * -1.0
           + sample_height(coords + vec2<i32>( 1, -1)) *  1.0
           + sample_height(coords + vec2<i32>( 1,  0)) *  2.0
           + sample_height(coords + vec2<i32>( 1,  1)) *  1.0;
           
    let gy = sample_height(coords + vec2<i32>(-1, -1)) * -1.0
           + sample_height(coords + vec2<i32>( 0, -1)) * -2.0
           + sample_height(coords + vec2<i32>( 1, -1)) * -1.0
           + sample_height(coords + vec2<i32>(-1,  1)) *  1.0
           + sample_height(coords + vec2<i32>( 0,  1)) *  2.0
           + sample_height(coords + vec2<i32>( 1,  1)) *  1.0;
    
    // Normal vector (pointing up, gradient down)
    var normal = vec3<f32>(-gx, -gy, 1.0);
    normal = normalize(normal);
    
    // Encode to [0,1] for storage
    let encoded = normal * 0.5 + 0.5;
    
    textureStore(output_texture, coords, vec4<f32>(encoded, 1.0));
}
```

### Parâmetros

| Parâmetro | Valor | Descrição |
|-----------|-------|-----------|
| Intensity (`normal_strength`) | 1.0 | Escala dos gradientes (multiplica gx, gy) |
| Flip Y (`normal_flip_y`) | uniform u32 | Inverte eixo Y da normal (F4.4) |

**Intensidade > 1.0:** Normais mais "fortes", superfície parece mais rugosa
**Intensidade < 1.0:** Normais mais "fracas", superfície mais plana

O flip de Y deixou de estar hard-coded: é controlado pelo uniform `normal_flip_y` (0/1), definido pela flag `--normal-format` (`opengl` = Y-up, default; `directx` = Y-down).

### Formatos de Normal Map

#### OpenGL (padrão do 2.0; Blender, Godot)
- Y-up: `normal.y` mantido
- Canais: RGB

#### DirectX (Unity)
- Y-down: `normal.y = -normal.y` (i.e. `normal_flip_y = 1`)
- Canais: RGB

Seleção via `--normal-format opengl|directx`.

## 3. Metallic Map Generation

### Objetivo

Detectar áreas metálicas na imagem difusa e gerar uma máscara em escala de cinza.

### Heurísticas de Metal

Metais puros (ouro, prata, cobre, ferro) têm características distintas:

1. **Saturação baixa:** Metais são cinzentos (exceto ouro/cobre)
2. **Luminância alta:** Metais refletem bem
3. **Matiz específico:** Ouro é amarelo, cobre é laranja

### Espaço HSL

Converte RGB para Hue/Saturation/Luminance para análise:

```wgsl
fn rgb_to_hsl(rgb: vec3<f32>) -> vec3<f32> {
    let max_val = max(max(rgb.r, rgb.g), rgb.b);
    let min_val = min(min(rgb.r, rgb.g), rgb.b);
    let delta = max_val - min_val;
    
    // Luminance
    let l = (max_val + min_val) * 0.5;
    
    // Saturation
    var s = 0.0;
    if (delta > 0.0) {
        s = delta / (1.0 - abs(2.0 * l - 1.0));
    }
    
    // Hue
    var h = 0.0;
    if (delta > 0.0) {
        if (max_val == rgb.r) {
            h = (rgb.g - rgb.b) / delta;
        } else if (max_val == rgb.g) {
            h = 2.0 + (rgb.b - rgb.r) / delta;
        } else {
            h = 4.0 + (rgb.r - rgb.g) / delta;
        }
        h = h / 6.0;
        if (h < 0.0) { h += 1.0; }
    }
    
    return vec3<f32>(h, s, l);
}
```

### Algoritmo de Detecção (detetor de dois níveis, F4.5)

O detetor antigo usava bandas de matiz sobrepostas (ouro/cobre eram subconjuntos uns dos outros). Em 2.0 foi redesenhado em dois níveis **não sobrepostos**:

1. **Grupo acromático** (saturação < 0.15): cobre aço, prata, alumínio, titânio, pewter, chrome e blue steel. Score = `smoothstep(0.4, 0.8, l) * (1 − s/0.15)`, com um *bonus* opcional para tons azulados (blue steel).
2. **Quatro bandas cromáticas** de matiz, mutuamente exclusivas:

| Banda | Range de hue (h) | Metais típicos |
|-------|------------------|----------------|
| Cobre | 0.00 – 0.06 | cobre |
| Bronze | 0.06 – 0.09 | bronze |
| Ouro | 0.09 – 0.14 | ouro |
| Latão | 0.14 – 0.17 | latão |

Cada banda tem score por luminância e saturação. O resultado é o máximo de todos os grupos.

```wgsl
fn detect_metallic(rgb: vec3<f32>) -> f32 {
    let hsl = rgb_to_hsl(rgb);
    let h = hsl.x;  // Hue [0,1]
    let s = hsl.y;  // Saturation [0,1]
    let l = hsl.z;  // Luminance [0,1]

    var metallic = 0.0;

    // (1) Grupo acromático (aço, prata, alumínio, titânio, pewter, chrome, blue steel)
    if (s < GRAY_METAL_SAT_MAX && l > GRAY_METAL_LUM_MIN) {
        let lum_factor = smoothstep(GRAY_METAL_LUM_MIN, 0.8, l);
        let sat_factor = 1.0 - smoothstep(0.0, GRAY_METAL_SAT_MAX, s);
        metallic = max(metallic, lum_factor * sat_factor);
    }

    // (2) Quatro bandas cromáticas não sobrepostas
    //     Cobre [0.00,0.06), Bronze [0.06,0.09), Ouro [0.09,0.14), Latão [0.14,0.17)
    if (s > 0.3 && l > 0.25) {
        var chroma = 0.0;
        if (h >= 0.00 && h < 0.06) { chroma = smoothstep(0.25, 0.5, l) * smoothstep(0.4, 0.8, s); }      // cobre
        else if (h >= 0.06 && h < 0.09) { chroma = smoothstep(0.25, 0.5, l) * smoothstep(0.35, 0.75, s); } // bronze
        else if (h >= 0.09 && h < 0.14) { chroma = smoothstep(0.3, 0.6, l) * smoothstep(0.3, 0.7, s); }    // ouro
        else if (h >= 0.14 && h < 0.17) { chroma = smoothstep(0.3, 0.6, l) * smoothstep(0.3, 0.7, s); }    // latão
        metallic = max(metallic, chroma);
    }

    return clamp(metallic, 0.0, 1.0);
}
```

### Local-variance damping (F2.5)

Metais puros têm **baixa variância local** de luminância; texturas não-metálicas (betão, pedra cinzenta) têm variância alta. Antes do `textureStore`, o shader amortiza a deteção em regiões texturizadas usando o uniform `metallic_local_variance_factor` (0..1, configurável via `--metallic-local-variance`):

```
final = metallic * (1.0 - metallic_local_variance_factor * local_luma_variance)
```

Isto reduz falsos positivos em superfícies ásperas/cinzentas sem afetar metais puros lisos.

### Pseudocódigo WGSL Completo

```wgsl
@compute @workgroup_size(8, 8)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let coords = vec2<i32>(gid.xy);
    let dims = textureDimensions(input_texture);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let color = textureLoad(input_texture, coords, 0).rgb;
    let metallic = detect_metallic(color);

    textureStore(output_texture, coords, vec4<f32>(metallic, 0.0, 0.0, 1.0));
}
```

### Parâmetros

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `metallic_scale` | preset | Multiplicador global do score metálico |
| `metallic_local_variance_factor` | preset | Damping por variância local (F2.5); `--metallic-local-variance` |
| Achromatic sat max | 0.15 | Saturação máxima do grupo acromático |
| Achromatic lum min | 0.4 | Luminância mínima do grupo acromático |
| Copper hue | 0.00–0.06 | Banda de cobre |
| Bronze hue | 0.06–0.09 | Banda de bronze |
| Gold hue | 0.09–0.14 | Banda de ouro |
| Brass hue | 0.14–0.17 | Banda de latão |

### Limitações

**Algoritmo atual (2.0):**
- Funciona bem para metais puros e as ligas comuns (aço/prata/alumínio/cobre/bronze/ouro/latão)
- Bronzes e latões já não são reportados como subconjuntos de cobre/ouro
- Pode ainda falhar para:
  - Metais sujos/oxidados
  - Metais pintados
  - Materiais muito misturados

**Melhorias futuras:**
- Machine learning (classificação por ML)
- Amostras de cor definidas pelo utilizador

## 4. Smoothness Map Generation

### Objetivo

Combinar difusa + metallic numa smoothness espacial.

### Algoritmo (F4.1 — espacial)

A smoothness deixou de ser apenas `base + metallic`. Agora combina três termos, incluindo o contraste local numa janela 5×5:

```
smoothness = base + metallic_boost * metallic - roughness_factor * local_contrast_5x5
```

- Regiões **texturizadas** (alto `local_contrast_5x5`) → menos smoothness (mais ásperas)
- Regiões **planas** → mais smoothness
- Comportamento do MVP preservado quando `smoothness_roughness_factor == 0`

Com `--roughness`, o CLI exporta `1 − smoothness` como mapa de roughness.

### Parâmetros

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `smoothness_base` | preset | Termo base |
| `smoothness_metallic_boost` | preset | Peso do metallic |
| `smoothness_roughness_factor` | preset | Peso do contraste local (F4.1); `--smoothness-roughness` |

## 5. Edge Map Generation

### Objetivo

Detectar bordas/vincos a partir da normal.

### Algoritmo (F1.1 — reescrito)

O shader antigo calculava `(diff_x + 0.5) * (diff_y + 0.5) * 2.0` com gradientes centrados em 0, produzindo uma saída quase plana (~0.5) em todo o lado. Agora usa a **magnitude do gradiente da normal** (amostras ±1 px em X e Y) com um limiar *smoothstep*:

```
grad_mag = length(dN_dx, dN_dy)
edge = smoothstep(threshold_low, threshold_high, grad_mag) * contrast
```

### Parâmetros

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `edge_contrast` | preset | Multiplicador de contraste; `--edge-contrast` |

## 6. AO Map Generation

### Algoritmo

Cavity-style a partir da height: 8 amostras em torno do pixel (raios 1 e 2 px), oclusão quando a altura da amostra > altura do centro; resultado invertido e escalado por `ao_depth_scale`. O AO por ray marching (normal+height) do Materialize original continua como trabalho futuro.

### Parâmetros

| Parâmetro | Default | Descrição |
|-----------|---------|-----------|
| `ao_depth_scale` | preset | Escala de profundidade; `--ao-depth-scale` |

## 7. Curvature Map Generation _(opt-in)_

### Objetivo

Curvatura convexa/côncava a partir da height, via Laplaciano. É o sétimo mapa, gerado apenas com `--include-curvature`.

### Algoritmo

Laplaciano da height numa vizinhança 3×3, normalizado para [0,1] à volta de 0.5:

- `0.5` = plano
- `> 0.5` = côncavo (frestas, cavidades)
- `< 0.5` = convexo (arestas salientes)

**Uso típico:** máscaras de desgaste (edge wear), oclusão de curvatura, pintura procedural.

## 8. Auto-deteção (CPU, `src/analyze.rs`)

Em `-p auto`, antes dos shaders, corre um pré-passo na CPU que calcula:

| Feature | Cálculo |
|---------|---------|
| Luminância | média / desvio-padrão |
| Saturação | média / desvio-padrão |
| Histograma de matiz | 12 bins |
| Densidade de edges | Sobel |
| Variância de contraste local | janela 5×5 |
| Tile MSE | bordas topo/fundo + esquerda/direita completas |
| Cobertura alpha | fracção de pixels transparentes |

Uma árvore de decisão mapeia o vetor de features para um preset (metal, skin, wood, stone, foliage, floor, default) + pontuação de confiança. Aplica ainda **auto-tile** (`seamless = 1` quando `tile_mse < 0.005`) e **auto-scale** (`height_contrast`/`normal_strength` ajustados pela `edge_density`). `materialize info <imagem>` imprime o relatório completo sem gerar mapas.

## Otimizações

### Performance

1. **Separable convolution:** Gaussian blur em 2 passos (H + V) em vez de kernel 2D
2. **Shared memory:** Carregar blocos para `workgroup` memory para acessos coalesced
3. **Texture cache:** Reutilizar texturas intermediárias
4. **Early exit:** Workgroups fora dos bounds retornam imediatamente

### Precisão

1. **R32Float para height:** Preservar precisão intermediária
2. **RGBA8Unorm para normal:** Suficiente para visualização
3. **R8Unorm para metallic:** Um canal, valores 0-1

## Referências

- [Sobel Operator](https://en.wikipedia.org/wiki/Sobel_operator)
- [Gaussian Blur](https://en.wikipedia.org/wiki/Gaussian_blur)
- [HSL Color Space](https://en.wikipedia.org/wiki/HSL_and_HSV)
- [Physically Based Rendering](https://pbr-book.org/)
- Materialize original shaders (HLSL/CG)
