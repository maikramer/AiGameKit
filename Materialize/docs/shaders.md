# Shaders WGSL

## Visão Geral

Os shaders do Materialize CLI são escritos em **WGSL** (WebGPU Shading Language), a linguagem nativa do wgpu. Cada mapa PBR tem seu próprio shader compute.

## Estrutura dos Shaders

### Localização

```
src/
└── shaders/
    ├── height.wgsl      # Height map generation
    ├── normal.wgsl      # Normal map from height
    ├── metallic.wgsl    # Metallic detection (two-tier)
    ├── smoothness.wgsl  # Smoothness (diffuse + metallic, spatial)
    ├── edge.wgsl        # Edge from normal gradient magnitude
    ├── ao.wgsl          # AO cavity-style from height
    └── curvature.wgsl   # Curvature (Laplacian of height) — opt-in
```

### Compilação

Shaders são embutidos no binário via `include_str!`:

```rust
// gpu.rs
const HEIGHT_SHADER: &str = include_str!("shaders/height.wgsl");
const NORMAL_SHADER: &str = include_str!("shaders/normal.wgsl");
const METALLIC_SHADER: &str = include_str!("shaders/metallic.wgsl");
const SMOOTHNESS_SHADER: &str = include_str!("shaders/smoothness.wgsl");
const EDGE_SHADER: &str = include_str!("shaders/edge.wgsl");
const AO_SHADER: &str = include_str!("shaders/ao.wgsl");
const CURVATURE_SHADER: &str = include_str!("shaders/curvature.wgsl");  // opt-in
```

Todos os shaders partilham o mesmo *uniform* de preset em `@group(1) @binding(0)` (ver secção seguinte).

Isso elimina dependências de arquivos externos em runtime.

## Uniform Params (struct partilhada, 64 bytes)

Todos os shaders recebem o mesmo *uniform* `Params` em `@group(1) @binding(0)`. O layout cresceu de 48 → 64 bytes em 2.0 (4 campos novos: `normal_flip_y`, `metallic_local_variance_factor`, `smoothness_roughness_factor`, `seamless`, mais *padding*). O bloco abaixo é a fonte de verdade (espelha `src/shaders/height.wgsl`):

```wgsl
struct Params {
    height_blur_radius_0: f32,           // 1
    height_blur_radius_1: f32,           // 2
    height_blur_radius_2: f32,           // 3
    height_contrast: f32,                // 4
    normal_strength: f32,                // 5
    normal_flip_y: u32,                  // 6  (NEW 2.0)
    metallic_scale: f32,                 // 7
    metallic_local_variance_factor: f32, // 8  (NEW 2.0)
    smoothness_base: f32,                // 9
    smoothness_metallic_boost: f32,      // 10
    smoothness_roughness_factor: f32,    // 11 (NEW 2.0)
    edge_contrast: f32,                  // 12
    ao_depth_scale: f32,                 // 13
    seamless: u32,                       // 14 (NEW 2.0)
    _pad0: f32,                          // 15
    _pad1: f32,                          // 16
}

@group(1) @binding(0)
var<uniform> params: Params;
```

Os valores vêm do `PresetParams` em Rust (`src/preset.rs`), podendo ser sobrepostos pelos *overrides* inline (`--height-contrast`, `--normal-strength`, `--metallic-local-variance`, `--smoothness-roughness`, etc.).

### `sample_coord` — clamp vs wrap (F2.4)

Todos os shaders de amostragem de vizinhança (height blur, Sobel, edge gradient, AO cavity, curvature Laplacian) passam as coordenadas por um helper partilhado que alterna entre clamp e wrap consoante `params.seamless`:

```wgsl
fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
    let d = vec2<i32>(dims);
    if (params.seamless == 1u) {
        return ((coords % d) + d) % d;              // wrap (tileável)
    }
    return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));  // clamp
}
```

`seamless` é definido por `--seamless`/`--no-seamless`, ou automaticamente por `-p auto` quando `tile_mse < 0.005`.

### Nota: `var` é palavra reservada em WGSL

`var` é uma palavra-chave reservada em WGSL (declara variáveis), por isso os identificadores de texturas usam nomes descritivos (`input_texture`, `height_texture`, `var input_texture: texture_2d<f32>;`). Nunca chamar uma variável simplesmente `var` — o compilador rejeita. Este bug foi atingido durante o desenvolvimento do 2.0.

## Shader: height.wgsl

### Propósito

Extrai mapa de altura a partir de imagem difusa usando multi-level blur + contraste.

### Entradas e Saídas

```wgsl
// Bind group 0
@group(0) @binding(0)
var input_texture: texture_2d<f32>;           // rgba8unorm

@group(0) @binding(1)
var output_texture: texture_storage_2d<r32float, write>;
```

### Constantes

```wgsl
const BLUR_LEVELS: i32 = 7;
const WEIGHTS: array<f32, 7> = array(0.5, 0.3, 0.15, 0.03, 0.015, 0.003, 0.002);
const CONTRAST: f32 = 1.5;
```

### Funções Auxiliares

#### RGB para Luminance

```wgsl
fn rgb_to_luminance(rgb: vec3<f32>) -> f32 {
    // Pesos ITU-R BT.709
    return dot(rgb, vec3<f32>(0.2126, 0.7152, 0.0722));
}
```

#### Gaussian 1D

```wgsl
fn gaussian_1d(x: f32, sigma: f32) -> f32 {
    let a = 1.0 / (sigma * sqrt(2.0 * 3.14159265));
    let b = exp(-(x * x) / (2.0 * sigma * sigma));
    return a * b;
}
```

#### Sample seguro com clamp

```wgsl
fn safe_sample(tex: texture_2d<f32>, coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let clamped = clamp(coords, vec2<i32>(0), vec2<i32>(dims) - vec2<i32>(1));
    return rgb_to_luminance(textureLoad(tex, clamped, 0).rgb);
}
```

#### Horizontal Blur

```wgsl
fn blur_horizontal(tex: texture_2d<f32>, center: vec2<i32>, sigma: f32, dims: vec2<u32>) -> f32 {
    var sum = 0.0;
    var weight_sum = 0.0;
    
    let radius = i32(ceil(sigma * 3.0));  // 99.7% da energia
    
    for (var x = -radius; x <= radius; x++) {
        let coords = center + vec2<i32>(x, 0);
        let weight = gaussian_1d(f32(x), sigma);
        sum += safe_sample(tex, coords, dims) * weight;
        weight_sum += weight;
    }
    
    return sum / weight_sum;
}
```

#### Vertical Blur

```wgsl
fn blur_vertical(tex: texture_2d<f32>, center: vec2<i32>, sigma: f32, dims: vec2<u32>) -> f32 {
    var sum = 0.0;
    var weight_sum = 0.0;
    
    let radius = i32(ceil(sigma * 3.0));
    
    for (var y = -radius; y <= radius; y++) {
        let coords = center + vec2<i32>(0, y);
        let weight = gaussian_1d(f32(y), sigma);
        sum += safe_sample(tex, coords, dims) * weight;
        weight_sum += weight;
    }
    
    return sum / weight_sum;
}
```

#### Contrast Enhancement

```wgsl
fn enhance_contrast(value: f32, contrast: f32) -> f32 {
    // Sigmoid curve
    let centered = value * 2.0 - 1.0;
    let enhanced = centered / (1.0 + exp(-contrast * centered));
    return clamp((enhanced + 1.0) * 0.5, 0.0, 1.0);
}
```

### Entry Point

```wgsl
@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(input_texture);
    let coords = vec2<i32>(global_id.xy);
    
    // Early exit se fora dos bounds
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    
    // Multi-level blur
    var height = 0.0;
    
    for (var level = 0; level < BLUR_LEVELS; level++) {
        let sigma = f32(1 << level);  // 1, 2, 4, 8, 16, 32, 64
        
        // Separable blur: horizontal then vertical
        let h_blur = blur_horizontal(input_texture, coords, sigma, dims);
        let blurred = blur_vertical(input_texture, coords, sigma, dims);
        
        // Usar o vertical no sample (simplificado para MVP)
        // Na prática precisaria de texture intermediária
        height += WEIGHTS[level] * h_blur;
    }
    
    // Apply contrast
    height = enhance_contrast(height, CONTRAST);
    
    // Store result
    textureStore(output_texture, coords, vec4<f32>(height, 0.0, 0.0, 1.0));
}
```

### Notas de Implementação

**Blur separável real:** Para blur separável eficiente em compute shaders, precisamos:
1. Passo 1: Blur horizontal, escrever em texture intermediária
2. Passo 2: Blur vertical na intermediária, escrever em output

Isso requer duas dispatch calls ou ping-pong entre duas textures.

**Para MVP:** Podemos usar um kernel 2D mais simples (mais lento mas funciona).

## Shader: normal.wgsl

### Propósito

Gera normal map a partir do height map usando operador Sobel.

### Entradas e Saídas

```wgsl
@group(0) @binding(0)
var height_texture: texture_2d<f32>;          // r32float

@group(0) @binding(1)
var output_texture: texture_storage_2d<rgba8unorm, write>;
```

### Constantes

```wgsl
const INTENSITY: f32 = 1.0;      // Escala dos gradientes
const FLIP_Y: bool = false;      // Flip Y para OpenGL
```

### Funções Auxiliares

#### Sample com padding

```wgsl
fn sample_height(coords: vec2<i32>, dims: vec2<u32>) -> f32 {
    let clamped = clamp(coords, vec2<i32>(0), vec2<i32>(dims) - vec2<i32>(1));
    return textureLoad(height_texture, clamped, 0).r;
}
```

#### Sobel Operator

```wgsl
fn sobel_gradient(center: vec2<i32>, dims: vec2<u32>) -> vec2<f32> {
    // Sobel X kernel
    let gx = sample_height(center + vec2<i32>(-1, -1), dims) * -1.0
           + sample_height(center + vec2<i32>(-1,  0), dims) * -2.0
           + sample_height(center + vec2<i32>(-1,  1), dims) * -1.0
           + sample_height(center + vec2<i32>( 1, -1), dims) *  1.0
           + sample_height(center + vec2<i32>( 1,  0), dims) *  2.0
           + sample_height(center + vec2<i32>( 1,  1), dims) *  1.0;
    
    // Sobel Y kernel
    let gy = sample_height(center + vec2<i32>(-1, -1), dims) * -1.0
           + sample_height(center + vec2<i32>( 0, -1), dims) * -2.0
           + sample_height(center + vec2<i32>( 1, -1), dims) * -1.0
           + sample_height(center + vec2<i32>(-1,  1), dims) *  1.0
           + sample_height(center + vec2<i32>( 0,  1), dims) *  2.0
           + sample_height(center + vec2<i32>( 1,  1), dims) *  1.0;
    
    return vec2<f32>(gx, gy);
}
```

#### Encode Normal

```wgsl
fn encode_normal(normal: vec3<f32>) -> vec3<f32> {
    // [ -1, 1 ] -> [ 0, 1 ]
    return normal * 0.5 + 0.5;
}
```

### Entry Point

```wgsl
@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);
    
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    
    // Calculate gradients
    let gradient = sobel_gradient(coords, dims);
    var gx = gradient.x * INTENSITY;
    var gy = gradient.y * INTENSITY;
    
    // Flip Y if needed (OpenGL vs DirectX)
    if (FLIP_Y) {
        gy = -gy;
    }
    
    // Reconstruct normal
    // normal points in -gradient direction, with z up
    var normal = vec3<f32>(-gx, -gy, 1.0);
    normal = normalize(normal);
    
    // Encode and store
    let encoded = encode_normal(normal);
    textureStore(output_texture, coords, vec4<f32>(encoded, 1.0));
}
```

### Formatos de Normal

#### DirectX (padrão do MVP)
```wgsl
// Y down in texture
let encoded = vec3<f32>(normal.x * 0.5 + 0.5,    // Red
                        -normal.y * 0.5 + 0.5,   // Green (inverted)
                        normal.z * 0.5 + 0.5);   // Blue
```

#### OpenGL
```wgsl
// Y up in texture
let encoded = vec3<f32>(normal.x * 0.5 + 0.5,    // Red
                        normal.y * 0.5 + 0.5,    // Green
                        normal.z * 0.5 + 0.5);   // Blue
```

## Shader: metallic.wgsl

### Propósito

Detecta áreas metálicas por análise de cor em espaço HSL.

### Entradas e Saídas

```wgsl
@group(0) @binding(0)
var input_texture: texture_2d<f32>;           // rgba8unorm (diffuse)

@group(0) @binding(1)
var output_texture: texture_storage_2d<r8unorm, write>;
```

### Constantes

```wgsl
// Thresholds para metais cinzentos
const GRAY_METAL_SAT_MAX: f32 = 0.15;
const GRAY_METAL_LUM_MIN: f32 = 0.4;

// Ranges de matiz (hue)
const GOLD_HUE_MIN: f32 = 0.08;
const GOLD_HUE_MAX: f32 = 0.15;
const COPPER_HUE_MIN: f32 = 0.02;
const COPPER_HUE_MAX: f32 = 0.08;
```

### Funções Auxiliares

#### RGB para HSL

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
            h = (rgb.g - rgb.b) / delta + select(0.0, 6.0, rgb.g < rgb.b);
        } else if (max_val == rgb.g) {
            h = (rgb.b - rgb.r) / delta + 2.0;
        } else {
            h = (rgb.r - rgb.g) / delta + 4.0;
        }
        h = h / 6.0;
    }
    
    return vec3<f32>(h, s, l);
}
```

#### Smoothstep

```wgsl
fn smoothstep(edge0: f32, edge1: f32, x: f32) -> f32 {
    let t = clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0);
    return t * t * (3.0 - 2.0 * t);
}
```

#### Detect Metallic

```wgsl
fn detect_metallic(rgb: vec3<f32>) -> f32 {
    let hsl = rgb_to_hsl(rgb);
    let h = hsl.x;
    let s = hsl.y;
    let l = hsl.z;
    
    var metallic = 0.0;
    
    // Metais cinzentos (prata, aço, alumínio)
    if (s < GRAY_METAL_SAT_MAX && l > GRAY_METAL_LUM_MIN) {
        let lum_factor = smoothstep(GRAY_METAL_LUM_MIN, 0.8, l);
        let sat_factor = 1.0 - smoothstep(0.0, GRAY_METAL_SAT_MAX, s);
        metallic = max(metallic, lum_factor * sat_factor);
    }
    
    // Ouro
    if (h >= GOLD_HUE_MIN && h <= GOLD_HUE_MAX && s > 0.3 && l > 0.3) {
        let hue_factor = 1.0 - abs(h - 0.115) * 10.0;  // peak at 0.115
        let lum_factor = smoothstep(0.3, 0.6, l);
        let sat_factor = smoothstep(0.3, 0.7, s);
        metallic = max(metallic, hue_factor * lum_factor * sat_factor);
    }
    
    // Cobre
    if (h >= COPPER_HUE_MIN && h <= COPPER_HUE_MAX && s > 0.4 && l > 0.25) {
        let hue_factor = 1.0 - abs(h - 0.05) * 20.0;  // peak at 0.05
        let lum_factor = smoothstep(0.25, 0.5, l);
        let sat_factor = smoothstep(0.4, 0.8, s);
        metallic = max(metallic, hue_factor * lum_factor * sat_factor);
    }
    
    return clamp(metallic, 0.0, 1.0);
}
```

### Entry Point

```wgsl
@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(input_texture);
    let coords = vec2<i32>(global_id.xy);
    
    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }
    
    let color = textureLoad(input_texture, coords, 0).rgb;
    let metallic = detect_metallic(color);
    
    textureStore(output_texture, coords, vec4<f32>(metallic, 0.0, 0.0, 0.0));
}
```

## Shader: curvature.wgsl _(opt-in)_

### Propósito

Gera o mapa de curvatura (convexo/côncavo) a partir da height via Laplaciano. Só é despachado quando `--include-curvature` está ativo.

### Entradas e Saídas

```wgsl
@group(0) @binding(0)
var height_texture: texture_2d<f32>;          // r32float

@group(0) @binding(1)
var output_texture: texture_storage_2d<r32float, write>;
```

### Algoritmo

Laplaciano da height numa vizinhança 3×3 (amostragem via `sample_coord`, respeitando `params.seamless`), normalizado para [0,1] centrado em 0.5:

- `0.5` = plano
- `> 0.5` = côncavo (frestas, cavidades)
- `< 0.5` = convexo (arestas salientes)

```wgsl
@compute @workgroup_size(8, 8, 1)
fn main(@builtin(global_invocation_id) global_id: vec3<u32>) {
    let dims = textureDimensions(height_texture);
    let coords = vec2<i32>(global_id.xy);

    if (coords.x >= i32(dims.x) || coords.y >= i32(dims.y)) {
        return;
    }

    let c  = textureLoad(height_texture, sample_coord(coords, dims), 0).r;
    let n  = textureLoad(height_texture, sample_coord(coords + vec2<i32>( 0, -1), dims), 0).r;
    let s  = textureLoad(height_texture, sample_coord(coords + vec2<i32>( 0,  1), dims), 0).r;
    let w  = textureLoad(height_texture, sample_coord(coords + vec2<i32>(-1,  0), dims), 0).r;
    let e  = textureLoad(height_texture, sample_coord(coords + vec2<i32>( 1,  0), dims), 0).r;

    let laplacian = (n + s + w + e) * 0.25 - c;   // >0: vizinhos mais altos (côncavo)
    let curvature = clamp(0.5 + laplacian, 0.0, 1.0);

    textureStore(output_texture, coords, vec4<f32>(curvature, 0.0, 0.0, 1.0));
}
```

## Bind Group Layouts

### Height Shader

```wgsl
// Bind group layout esperado pelo Rust:
// Binding 0: input_texture (sampled, rgba8unorm)
// Binding 1: output_texture (storage, r32float)
```

### Normal Shader

```wgsl
// Binding 0: height_texture (sampled, r32float)
// Binding 1: output_texture (storage, rgba8unorm)
```

### Metallic Shader

```wgsl
// Binding 0: input_texture (sampled, rgba8unorm)
// Binding 1: output_texture (storage, r8unorm)
```

## Workgroup Size

Todos os shaders usam:

```wgsl
@compute @workgroup_size(8, 8, 1)
```

Isso significa:
- 8 threads em X
- 8 threads em Y
- 1 thread em Z
- Total: 64 threads por workgroup

**Rationale:** 64 é um warp size comum (NVIDIA: 32, AMD: 64). 8x8 é bom para imagens 2D.

## Debug

### Print em WGSL

WGSL não tem `printf`, mas podemos usar:

1. **Output intermediate texture:** Escrever valores de debug em texture extra
2. **Buffer de staging:** Ler valores específicos de volta para CPU
3. **Validation layers:** wgpu mostra erros de shader

### Validação

```bash
# Validar sintaxe WGSL
cargo run --features wgsl_validate
# ou use: naga shader.wgsl
```

## Recursos WGSL

- [WGSL Spec](https://www.w3.org/TR/WGSL/)
- [WebGPU Fundamentals](https://webgpufundamentals.org/)
- [Naga](https://github.com/gfx-rs/naga) - Shader compiler usado pelo wgpu
