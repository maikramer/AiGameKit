# Arquitetura

## Visão Geral

O Materialize CLI segue uma arquitetura em camadas com processamento GPU via compute shaders:

```
┌────────────────────────────────────────────────────────────────┐
│                         CLI (main.rs)                         │
│                    clap para argumentos                        │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│                      Pipeline (pipeline.rs)                    │
│  Orquestra: Diffuse → Height → Normal → Metallic → Smoothness │
│              → Edge → AO                                       │
│  Gerencia dependências entre mapas (Height necessário p/ Normal) │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│                      GPU Context (gpu.rs)                      │
│  - Instance/Adapter/Device/Queue (wgpu)                         │
│  - Texture management (input/output buffers)                    │
│  - Compute pipeline setup                                       │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│                    Compute Shaders (WGSL)                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │ height   │ │  normal  │ │ metallic │ │smoothness│ │   edge   │ │    ao    │  │
│  │  .wgsl   │ │  .wgsl   │ │  .wgsl   │ │  .wgsl   │ │  .wgsl   │ │  .wgsl   │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
└────────────────────────┬───────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────────┐
│                      I/O (io.rs)                               │
│  - image crate: PNG/JPG/TGA/BMP/EXR                             │
│  - GPU↔CPU texture transfer                                     │
└────────────────────────────────────────────────────────────────┘
```

## Componentes

### 1. CLI (`main.rs` + `cli.rs`)

**Responsabilidade:** Parse de argumentos e orquestração de alto nível

**Tecnologia:** [clap](https://github.com/clap-rs/clap) com derive macros

**Fluxo:**
1. Parse argumentos
2. Validar input
3. Inicializar GPU
4. Executar pipeline
5. Salvar outputs
6. Reportar resultado

### 2. Pipeline (`pipeline.rs`)

**Responsabilidade:** Coordenar a execução dos shaders na ordem correta

**Fluxo de processamento:**
```
Diffuse Input
      │
      ▼
┌─────────────┐
│  Height     │ ──► height_texture (R32Float)
│  Shader     │
└─────────────┘
      │
      ▼
┌─────────────┐
│  Normal     │ ──► normal_texture (RGBA8Unorm)
│  Shader     │     (usa height como input)
└─────────────┘
      │
      ▼
┌─────────────┐
│  Metallic   │ ──► metallic_texture (R8Unorm)
│  Shader     │     (usa diffuse original)
└─────────────┘
      │
      ▼
┌─────────────┐
│  Smoothness │ ──► smoothness_texture (R8Unorm)
│  Shader     │     (usa diffuse + metallic)
└─────────────┘
      │
      ▼
┌─────────────┐
│    Edge     │ ──► edge_texture (R8Unorm)
│   Shader    │     (usa normal como input)
└─────────────┘
      │
      ▼
┌─────────────┐
│     AO      │ ──► ao_texture (R8Unorm)
│   Shader    │     (usa height como input)
└─────────────┘
```

**Otimização:** Shaders executam em sequência sem readback CPU intermediário

### 3. GPU Context (`gpu.rs`)

**Responsabilidade:** Abstrair interação com wgpu

**API Pública:**
```rust
pub struct GpuContext {
    device: wgpu::Device,
    queue: wgpu::Queue,
}

impl GpuContext {
    pub async fn new() -> Result<Self>;
    pub fn create_texture(&self, size: Extent3d, format: TextureFormat) -> Texture;
    pub fn create_compute_pipeline(&self, shader: &str, entry_point: &str) -> ComputePipeline;
    pub fn dispatch(&self, pipeline: &ComputePipeline, bind_group: &BindGroup, workgroups: (u32, u32, u32));
    pub fn read_texture(&self, texture: &Texture) -> Vec<u8>;
}
```

**Configuração wgpu:**
- Backend: Vulkan (Linux), Metal (macOS), DX12 (Windows)
- Power preference: High performance
- Limits: Default
- Features: Shader float32 filtering (se disponível)

### 4. Shaders WGSL (`src/shaders/`)

**Responsabilidade:** Processamento de imagem em GPU

**Estrutura:** Cada shader é um arquivo `.wgsl` independente

**Compilação:** Shaders são embutidos no binário via `include_str!()`

**Workgroup size:** 8x8x1 (64 threads por workgroup, bom equilíbrio para GPUs modernas)

### 5. I/O (`io.rs`)

**Responsabilidade:** Leitura e escrita de imagens

**Formatos suportados:**

| Formato | Leitura | Escrita | Observação |
|---------|---------|---------|------------|
| PNG     | ✓       | ✓       | Recomendado (lossless) |
| JPEG    | ✓       | ✓       | Lossy, configurável |
| TGA     | ✓       | ✓       | Games legacy |
| BMP     | ✓       | ✗       | Leitura apenas |
| EXR     | ✓       | ✓       | HDR, recomendado para normais |

**Tecnologia:** [image crate](https://github.com/image-rs/image)

**Conversão de formatos:**
- RGBA8Unorm (u8) para R32Float (f32) - upload para GPU
- R32Float/RGBA8Unorm para bytes - download da GPU

## Fluxo de Dados

### Upload (CPU → GPU)

```
Imagem PNG/JPG (CPU)
       │
       ▼
┌──────────────┐
│ image crate  │ ──► DynamicImage
│  (decode)    │
└──────────────┘
       │
       ▼
┌──────────────┐
│  to_rgba8()  │ ──► Vec<u8> RGBA
└──────────────┘
       │
       ▼
┌──────────────┐
│ wgpu::Queue  │ ──► write_texture()
│   (upload)   │
└──────────────┘
       │
       ▼
GPU Texture (RGBA8Unorm)
```

### Processamento (GPU)

```
input_texture (RGBA8Unorm)
          │
          ▼
     ┌─────────┐
     │ Height  │ ──► height_texture (R32Float)
     │ Shader  │
     └────┬────┘
          │
          ▼
     ┌─────────┐
     │ Normal  │ ──► normal_texture (RGBA8Unorm)
     │ Shader  │
     └────┬────┘
          │
          ▼
     ┌─────────┐
     │Metallic │ ──► metallic_texture (R8Unorm)
     │ Shader  │
     └────┬────┘
          │
          ▼
     ┌─────────┐
     │Smoothness│ ──► smoothness_texture (R8Unorm)
     │ Shader  │
     └────┬────┘
          │
          ▼
     ┌─────────┐
     │  Edge   │ ──► edge_texture (R8Unorm)
     │ Shader  │
     └────┬────┘
          │
          ▼
     ┌─────────┐
     │   AO    │ ──► ao_texture (R8Unorm)
     │ Shader  │
     └─────────┘
```

### Download (GPU → CPU)

```
output_texture (GPU)
       │
       ▼
┌──────────────┐
│ CommandEncoder │ ──► copy_texture_to_buffer()
│   (encode)   │
└──────────────┘
       │
       ▼
┌──────────────┐
│ wgpu::Queue  │ ──► submit()
│   (submit)   │
└──────────────┘
       │
       ▼
┌──────────────┐
│ buffer.slice() │ ──► get_mapped_range()
│  (map_async) │
└──────────────┘
       │
       ▼
Vec<u8> (CPU)
       │
       ▼
┌──────────────┐
│ image crate  │ ──► save()
│   (encode)   │
└──────────────┘
       │
       ▼
   Arquivo PNG
```

## Estrutura de Diretórios

```
materialize-cli/
├── Cargo.toml
├── README.md
├── docs/
│   └── (documentação)
├── src/
│   ├── main.rs          # Entry point
│   ├── cli.rs           # CLI argument parsing
│   ├── pipeline.rs      # Pipeline orquestração
│   ├── gpu.rs           # GPU abstraction
│   ├── io.rs            # Image I/O
│   ├── preset.rs        # Material presets
│   └── shaders/
│       ├── height.wgsl
│       ├── normal.wgsl
│       ├── metallic.wgsl
│       ├── smoothness.wgsl
│       ├── edge.wgsl
│       └── ao.wgsl
└── tests/
    └── integration_test.rs
```

## Dependências

### Runtime

| Crate | Versão | Propósito |
|-------|--------|-----------|
| wgpu | 29 | Compute shaders GPU |
| pollster | 0.4 | Runtime async blocking |
| image | 0.25 | Decode/encode de imagens |
| clap | 4.6 | CLI argument parsing |
| anyhow | 1.0 | Error handling |

### Dev

| Crate | Versão | Propósito |
|-------|--------|-----------|
| tempfile | 3.27 | Arquivos temporários para testes |

## Decisões de Design

### Por que wgpu e não OpenGL direto?

- **Moderno:** API unificada para todas as plataformas
- **Compute shaders:** Projetado para GPGPU, não só gráficos
- **Seguro:** Validação em tempo de compilação e runtime
- **Futuro:** Baseado no padrão WebGPU, futuro-proof

### Por que compute shaders e não fragment shaders?

- **Direto:** Sem precisar de render pipeline, vertex buffers, framebuffers
- **Simples:** Um shader = uma função de compute
- **Eficiente:** Sem overhead de rasterização

### Por que Rust?

- **Performance:** Zero-cost abstractions, controle de memória
- **Segurança:** Ownership evita data races naturais em GPU code
- **Ecossistema:** wgpu é primariamente Rust
- **Deploy:** Binário único, sem runtime

## Considerações de Performance

### Otimizações aplicadas:

1. **Minimize CPU↔GPU transfers:** Apenas upload inicial e download final
2. **Texture arrays:** Reutilize textures intermediárias se possível
3. **Workgroup size:** 8x8 = 64 threads (warpsize comum)
4. **Formatos eficientes:** R32Float para height, R8Unorm para metallic

### Possíveis melhorias futuras:

1. **Tiled processing:** Para imagens maiores que GPU memory
2. **Async pipeline:** Paralelizar upload/process/download de múltiplas imagens
3. **Mipmap chain:** Usar mips para blur multi-level mais eficiente
