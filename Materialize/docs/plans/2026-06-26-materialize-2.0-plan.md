# Plano: Materialize-CLI 2.0 — Quality + Auto + UX (v2 — pós-Momus)

**Data:** 2026-06-26 (v2)
**Versão alvo:** 2.0.0 (major bump)
**Escopo user:** F1 (bugs) + F2 (auto-detecção) + F3 (CLI/UX) + F4 (qualidade maps)
**Fora do escopo:** golden image tests automatizados (F5), perf separable blur (F6), AO ray-march (Q2), TOML config (U2), tiled processing (P1), serve mode (P5).

**Decisões de produto (não-negociáveis):**
- Auto-preset via **heurística CPU** (pré-pass amostragem + bordas completas).
- **Sem restrições de compat** — bump 2.0, defaults podem mudar, breaking changes documentados.
- **Auto-tile** só ativa wrap sampling quando detectado (`tile_mse < threshold` calibrado em corpus).
- **Exit codes** 6 códigos completos.
- **GPU concurrency** = 1 por device; `--jobs N` paraleliza apenas estágios CPU (load/save/analyze); GPU fica num único thread dedicado via canal.

---

## ⚠️ Breaking changes em 2.0 (lista completa)

| # | Mudança | Impacto | Migration |
|---|---|---|---|
| BC1 | Exit codes granulares (0/1/2/3/4/5/6) em vez de só 0/1 | Scripts que fazem `== 1` podem mudar comportamento em erros específicos | Checar `!= 0` (recomendado); mapear 2/3/4/5/6 se quiser granularidade |
| BC2 | `PresetParams` 48→64 bytes; `struct Params` WGSL atualizada em todos shaders | Reprocessamento transparente; não afeta CLI | Nenhuma (interno) |
| BC3 | `Preset::Auto` adicionado (novo valor de `-p`) | Aditivo; não quebra | N/A |
| BC4 | 12 novos presets (aditivo) | Aditivo | N/A |
| BC5 | `--quality` mantém `0..=100`; se `0`, vira `1` + warning em `-v` | Compatível (não rejeita) | N/A |
| BC6 | Novas flags opt-in (`--only`, `--skip`, `--roughness`, `--normal-format`, `--include-curvature`, `--seamless`, `--no-seamless`, `--jobs`, inline overrides, `--list-presets`, `--list-maps`, `--info`, `--generate-completions`) | Aditivo | N/A |
| BC7 | **Curvature map é OPT-IN** (`--include-curvature`); output default continua 6 maps | Compatível por padrão | Quiser curvature: passar `--include-curvature` |
| BC8 | `materialize info <image>` vira **subcomando** (não flag) | CLI surface aditivo | N/A |
| BC9 | `MATERIALIZE_GPU_BACKEND` / `MATERIALIZE_LOG` finalmente implementados (doc-truth) | Compatível | N/A |

**Decisões explícitas pós-Momus:**
- Curvature = **opt-in** (resolve M8).
- `ao-mode` flag **removida** do F3.2 (era Q2 fora-de-escopo) (resolve B4).
- F1.7 (poll fix) **dropado** — era falso bug, wgpu requer poll para `map_async` resolver; `pipeline.rs:236` + `gpu.rs:450` servem propósitos distintos (compute-done vs map-done) (resolve B2).
- F1.6 reclassificado de "bug" para **defensive hardening** (NaN não é alcançável porque `delta > 0` é guardado antes da divisão) (resolve M6).
- `PresetParams` bumped para **64 bytes** com novo layout (resolve B3).

---

## F0 — Sprint 0: Refactor struct (PREREQUISITO DE TUDO)

### F0.1 — Bump `PresetParams` 48 → 64 bytes
**Layout final (16 × 4 bytes = 64 bytes, múltiplo de 16, alinhamento uniform OK):**
```rust
#[repr(C)]
#[derive(Copy, Clone, Debug, bytemuck::Pod, bytemuck::Zeroable)]
pub struct PresetParams {
    // Height
    pub height_blur_radius_0: f32,             // 1
    pub height_blur_radius_1: f32,             // 2
    pub height_blur_radius_2: f32,             // 3
    pub height_contrast: f32,                  // 4
    // Normal
    pub normal_strength: f32,                  // 5
    pub normal_flip_y: u32,                    // 6  NOVO (0 = OpenGL Y-up, 1 = DirectX Y-down)
    // Metallic
    pub metallic_scale: f32,                   // 7
    pub metallic_local_variance_factor: f32,   // 8  NOVO (F2.5, 0..1)
    // Smoothness
    pub smoothness_base: f32,                  // 9
    pub smoothness_metallic_boost: f32,        // 10
    pub smoothness_roughness_factor: f32,      // 11 NOVO (F4.1)
    // Edge
    pub edge_contrast: f32,                    // 12
    // AO
    pub ao_depth_scale: f32,                   // 13
    // Mode flags
    pub seamless: u32,                         // 14 NOVO (F2.4: 0 = clamp, 1 = wrap)
    pub _pad0: f32,                            // 15
    pub _pad1: f32,                            // 16
}
```
**Tasks:**
- Atualizar `preset.rs::PresetParams` (layout acima).
- Atualizar `struct Params {…}` em **TODOS** os 6 shaders (`height.wgsl`, `normal.wgsl`, `metallic.wgsl`, `smoothness.wgsl`, `edge.wgsl`, `ao.wgsl`) + curvature.wgsl novo — **idênticos em orddem e tipo** ao Rust `#[repr(C)]`.
- Atualizar `test_preset_params_size` (`preset.rs:197`): `assert_eq!(size_of::<PresetParams>(), 64)`.
- Atualizar `params()` em todos os 19 presets (default + skin + floor + metal + fabric + wood + stone + 12 novos) para inicializar os 3 novos campos com defaults sane (0 = OpenGL, 0 = sem local variance damp, 0 = sem roughness spatial, 0 = clamp).
- Commit standalone verde antes de qualquer trabalho F1+.

**Aceitação:**
- `cargo build` limpo, `cargo test test_preset_params_size` passa.
- `cargo test test_preset_roundtrip_str` passa (excluir `Preset::Auto` de `ALL` se necessário).
- Smoke: `cargo run -- tests/fixtures/diffuse.png -o /tmp/out -v` gera 6 maps idênticos em pixels ao v1 (regression baseline).

---

## F1 — Bugs & doc-truth

### F1.1 — Rewrite `edge.wgsl` (CRÍTICO) — Bug real confirmado
**Bug confirmado (`edge.wgsl:46-51`):** `(diff_x + 0.5) * (diff_y + 0.5) * 2.0` onde `diff_*` são gradientes centrados em 0 → output ~0.5 sempre.
**Fix (gradient magnitude):**
```wgsl
let gx = n_x_plus.r - n_x_minus.r;
let gy = n_y_plus.g - n_y_minus.g;
let mag = sqrt(gx*gx + gy*gy);
let edge = smoothstep(0.05, 0.40, mag * params.edge_contrast);
```
**Aceitação:** smoke visual (gradient real), + teste de regressão: fixture com borda vertical forte produz linha branca no edge map (pixel max > 200), vs fixture plana produz valor baixo (pixel max < 30).

### F1.2 — Exit codes granulares (CRÍTICO)
**Bug (`main.rs:14`):** sempre `exit(1)`. Doc diz 6 códigos.
**Implementação:**
```rust
#[derive(Debug)]
pub enum MaterializeError {
    NotFound(String),
    UnsupportedFormat(String),
    Gpu(String),
    Io(String),
    TooLarge { width: u32, height: u32, bytes: u64 },
    Other(anyhow::Error),
}

impl From<anyhow::Error> for MaterializeError { /* classifica por mensagem/tipo */ }
impl From<std::io::Error> for MaterializeError { ... Gpu vs Io ... }
impl From<image::ImageError> for MaterializeError { ... UnsupportedFormat ... }

impl MaterializeError {
    pub fn exit_code(&self) -> u8 {
        match self {
            Self::NotFound(_) => 2,
            Self::UnsupportedFormat(_) => 3,
            Self::Gpu(_) => 4,
            Self::Io(_) => 5,
            Self::TooLarge { .. } => 6,
            Self::Other(_) => 1,
        }
    }
}
// main.rs: std::process::exit(err.exit_code());
```
**Tasks:**
- `src/error.rs` novo com enum + conversions.
- `io::load_image` retorna `NotFound` em vez de anyhow bail.
- `gpu::GpuContext::new` e `Pipeline::process` propagam `Gpu`/`TooLarge`.
- `io::save_image` propaga `Io`.
- `main.rs::run()` retorna `Result<(), MaterializeError>`.
**Aceitação (automatizada, M9):** teste parametrizado em `tests/integration_test.rs` invocando binário e checando exit code: `nonexistent.png → 2`, `corrupt.bmp → 3` (ou mock adapter para 4), `/proc/1/root/forbidden/output.png → 5`.

### F1.3 — Env vars (`MATERIALIZE_GPU_BACKEND`, `MATERIALIZE_LOG`)
**Bug:** documentados em `cli-api.md:217` mas não lidos.
**Implementação:**
- `MATERIALIZE_GPU_BACKEND`: em `GpuContext::new()`, mapear `vulkan|metal|dx12|gl` → `wgpu::Backends` e passar em `InstanceDescriptor::new().backends`. Default: `PRIMARY` (all).
- `MATERIALIZE_LOG`: níveis `error|warn|info|debug|trace`. Adicionar dep leve `env_logger` (ou `tracing` minimal). Inicializar em `main`. Default: `warn`.
**Aceitação:** teste: `MATERIALIZE_GPU_BACKEND=invalid cargo run -- …` retorna 4 com mensagem clara; `MATERIALIZE_LOG=debug` produz output de debug em stderr.

### F1.4 — Shell completions
**Bug:** `--generate-completions` documentado mas ausente.
**Implementação:** dep `clap_complete`. Flag `--generate-completions <SHELL>` (bash|zsh|fish|elvish|powershell), imprime script em stdout, `exit(0)`. Curto-circuita antes do input check (ver F1.6-control-flow).
**Aceitação:** `cargo run -- --generate-completions bash | head -1` contém `_materialize` ou `materialize`.

### F1.5 — Doc-truth formatos
**Não-bug:** pipeline usa `Rgba8Unorm` interno em tudo (storage R8Unorm write tem suporte restrito em alguns backends); output grayscale via `channel_r8_to_image` (já feito). Atualizar **docs** (`architecture.md`, `shaders.md`) para refletir realidade em vez de "R8Unorm".
**Aceitação:** diff dos docs reflete código; `docs/shaders.md` mostra `rgba8unorm` para metallic.

### F1.6 — (ex-NaN) → reclassificado defensive hardening
**Status pós-Momus:** NaN **não alcançável** — `metallic.wgsl:33` guarda `if (delta > 0.0)` antes da divisão, e `delta==0 ⟺ denom==0`. Não é bug.
**Tasks (hardening opcional):** adicionar `let denom = max(1e-6, 1.0 - abs(2.0*l - 1.0));` em defensive. Não conta como bug fix.
**Aceitação:** smoke `metallic_map` em pixel preto/branco não produz NaN.

### F1.7 — **DROPADO** (Momus B2: era falso bug)
`pipeline.rs:236` (poll compute done) + `gpu.rs:450` (poll map done) são necessários em wgpu nativo; remover causaria deadlock. Sem mudança.

### F1.8 — Verbose timing real
**Bug:** doc mostra "done (45ms)" mas não mede.
**Implementação:** `Instant::now()` por estágio. `Pipeline::process` retorna `StageTimings { height_ms, normal_ms, metallic_ms, smoothness_ms, edge_ms, ao_ms, curvature_ms, readback_ms, total_ms }`. Print em `-v`.
**Aceitação:** output `-v` contém `ms)` por estágio.

### F1.9 — Quality consistency
**Status pós-Momus:** manter `0..=100` no `value_parser`. Em `io::save_image`, `quality.clamp(1, 100)` fica (encoder JPEG rejeita 0); em `-v`, warning se quality == 0. Não é breaking.
**Aceitação:** `-q 0 -v` produz warning e gera JPEG com qualidade 1.

---

## F2 — Auto-detecção (heurística CPU)

### F2.1 — Módulo `src/analyze.rs`
**Pré-pass CPU.** Amostragem **estratificada** para features interiores + **bordas completas** (top↔bottom, left↔right rows/cols inteiros) para `tile_mse` (resolve m5).
**API:**
```rust
pub struct ImageFeatures {
    pub luma_mean: f32,               // [0,1]
    pub luma_std: f32,
    pub sat_mean: f32,                // [0,1]
    pub sat_std: f32,
    pub hue_hist: [u32; 12],          // 12 bins × 30° (count of pixels with sat>0.1)
    pub edge_density: f32,            // fraction of subsampled pixels with |Sobel|>t
    pub local_contrast_variance: f32, // mean of per-window luma variance (5×5)
    pub tile_mse: f32,                // mean MSE of opposite borders (full rows/cols)
    pub alpha_coverage: f32,          // fraction alpha<1.0
}
pub fn analyze(image: &DynamicImage) -> ImageFeatures;
```
**Sampling:** grid N=10000 interior + todas as bordas (top, bottom, left, right rows/cols). ~50ms para 4K.
**Aceitação:** test com fixture 100% branco → `luma_mean ≈ 1.0, sat_mean ≈ 0, edge_density ≈ 0`; fixture gradiente → `edge_density > 0.3`; fixture tileable (repetida) → `tile_mse ≈ 0`.

### F2.2 — `classify()` — decision tree com thresholds calibrados
**Calibração (resolve M4):** corpus `tests/fixtures/classification/*.png` com 1+ amostra por preset (19 presets → ≥19 fixtures, idealmente 3 cada = 57). Medir features de cada uma, escolher thresholds pelo ponto de operação que separa classes no corpus. Thresholds congelados + test de regressão.
**Decision tree (predicados NUMÉRICOS explícitos):**
```
if sat_mean < 0.15 and luma_mean > 0.40 and hue_hist[gray_dominant] > 0.6:
    → metal (gray)         # steel/aluminum/silver/titanium/pewter/chrome subsumido
elif sat_mean > 0.30 and 0.06 < hue_peak <= 0.16 and luma_mean > 0.30:
    → metal (chromatic)    # gold (0.08-0.14) / copper (0.02-0.07) / bronze (0.05-0.10) / brass (0.10-0.14)
elif edge_density < 0.05 and local_contrast_variance < 0.003 and sat_mean < 0.25:
    → skin                 # pele: baixa variação local, baixa sat
elif local_contrast_variance > 0.015 and edge_density > 0.20 and 0.08 <= hue_peak <= 0.13:
    → wood                 # grão: alto contraste local, hue castanho/amarelo
elif edge_density > 0.25 and sat_mean < 0.18 and luma_mean < 0.45:
    → stone                # escuro, áspero, cinza
elif sat_mean > 0.18 and 0.22 <= hue_peak <= 0.40:
    → foliage              # verde
elif tile_mse < 0.005 and local_contrast_variance > 0.015:
    → floor                # tileable + estruturado
else:
    → default
```
**Confidence:** baseada em distância normalizada ao threshold (ex: para `sat_mean < 0.15`, `conf = clamp((0.15 - sat_mean) / 0.15, 0, 1)`).
**Output struct:**
```rust
pub struct Classification { pub preset: Preset, pub confidence: f32, pub features: ImageFeatures }
pub fn classify(f: &ImageFeatures) -> Classification;
```
**Aceitação (M1, automatizada):** teste `tests/test_analyze.rs` itera corpus com `expected_preset.txt` lado-a-lado de cada fixture; `classify` deve acertar ≥90% do corpus. Report se abaixo.

### F2.3 — `-p auto` integração
- Adicionar `Preset::Auto` ao enum (excluir de `ALL`).
- Em `main.rs`: se `args.preset == Auto`, rodar `analyze::analyze(&image)` → `classify()` → resolver para preset concreto; print `"Auto-detected: metal (confidence 0.82)"` em `-v`.
- `PresetParams` do preset resolvido + **auto-scale** (A2): ajustar `height_contrast` e `normal_strength` por `edge_density`:
  - `effective_height_contrast = base * (0.7 + 0.6 * edge_density)`  (texturas detalhadas → mais contraste)
  - `effective_normal_strength = base * (1.2 - edge_density)` (texturas muito ruidosas → suavizar normals)
**Aceitação:** `cargo run -- fixture.png -p auto -v` printa preset detectado; mesmo fixture com preset explícito do detectado gera pixels próximos (< 5% diff).

### F2.4 — Auto-tile wrap sampling
**Implementação (resolves m1, m2):**
- `tile_mse < 0.005` (calibrado em corpus) → `seamless = 1`.
- Override: `--seamless` força 1, `--no-seamless` força 0.
- **4 shaders** com sampling de vizinhança:
  - `height.wgsl::simple_blur` (usa in-loop bounds check `if sample_coords in bounds`) → reestruturar para usar helper novo.
  - `normal.wgsl::sample_height`, `edge.wgsl::sample_normal_rg`, `ao.wgsl::sample_height` (usam `clamp`) → trocar por helper.
- Helper WGSL unificado (em cada shader, já que WGSL não tem includes):
  ```wgsl
  fn sample_coord(coords: vec2<i32>, dims: vec2<u32>) -> vec2<i32> {
      let d = vec2<i32>(dims);
      if (params.seamless == 1u) {
          return ((coords % d) + d) % d;  // Euclidean modulo, signed-safe
      }
      return clamp(coords, vec2<i32>(0), d - vec2<i32>(1));
  }
  ```
  (Tipo-correto: `coords` e `d` ambos `vec2<i32>`.)
- Metallic/smoothness/curvature não mudam (sample single pixel).
**Aceitação:** teste: fixture tileable (repetida 2×2) gera normal/AO maps sem seam visível; MSE entre mapas de input tileable vs input com wrap forçado difere < 1%.

### F2.5 — Auto-metallic refinement (fórmula concreta, resolves M5)
**Shader metallic.wgsl:** após `detect_metallic(rgb)`, computar local variance 3×3 em luminância:
```wgsl
fn local_luma_variance_3x3(center: vec2<i32>, dims: vec2<u32>) -> f32 {
    var sum = 0.0; var sum_sq = 0.0; var n = 0.0;
    for (var dy = -1; dy <= 1; dy++) {
        for (var dx = -1; dx <= 1; dx++) {
            let c = sample_coord(center + vec2<i32>(dx, dy), dims);
            let luma = dot(textureLoad(input_texture, c, 0).rgb, vec3(0.2126, 0.7152, 0.0722));
            sum += luma; sum_sq += luma*luma; n += 1.0;
        }
    }
    let mean = sum / n;
    return clamp((sum_sq / n) - mean*mean, 0.0, 0.25);  // cap at 0.25 (max plausible)
}
// ...
let var = local_luma_variance_3x3(coords, dims);
let variance_factor = params.metallic_local_variance_factor; // 0..1, default 0.5
let damping = 1.0 - variance_factor * clamp(var * 4.0, 0.0, 1.0);
let metallic = clamp(raw * params.metallic_scale * damping, 0.0, 1.0);
```
**Intuição:** metais puros têm reflexão especular baixa variação local → `var` baixa → `damping ≈ 1`. Cinzas texturizados (cimento, pedra cinza) têm alta variação → `damping < 1` reduz falsos positivos.
**Aceitação:** teste: fixture metal_cinza_liso vs cimento_cinza_texturizado: metallic_mean do metal ≥ 5× o do cimento.

---

## F3 — CLI/UX

### F3.1 — Batch dir/glob (com GPU thread dedicada, resolves M3)
**Arquitetura:**
- `INPUT` aceita arquivo, diretório, ou glob (`./textures/*.png`).
- Thread único "GPU owner" recebe trabalhos via canal `mpsc::channel<Job>`. CPU-side load/save/analyze rodam em pool `rayon` (ou `tokio::task::spawn_blocking`) com `--jobs N` workers.
- `--jobs N` controla paralelismo CPU apenas; GPU processa serialmente do canal.
- `--skip-existing` (resume): pula se `{name}_height.{ext}` existe.
- Progress: `[1/50] processing stone.png... done (120ms)` em stdout, ou `indicatif` bar com `--progress`.
**Tasks:**
- `src/batch.rs` novo com `BatchRunner`, `Job`, `BatchResult { processed, skipped, failed }`.
- `Pipeline` não muda (`&self`, chamado pelo GPU thread).
**Aceitação:** `cargo run -- ./tests/fixtures/classification/ -o /tmp/out --jobs 4 -v` processa todos; exit code reflete failures agregadas; output sequencial por causa GPU.

### F3.2 — Inline overrides
**Flags adicionadas em `cli.rs`:**
`--height-contrast <f>`, `--height-blur <f>` (aplica aos 3 radii como offsets), `--normal-strength <f>`, `--normal-format <opengl|directx>`, `--metallic-scale <f>`, `--smoothness-base <f>`, `--smoothness-boost <f>`, `--smoothness-roughness <f>`, `--edge-contrast <f>`, `--ao-depth-scale <f>`, `--metallic-local-variance <f>`.
Cada um `Option<f32>` (ou `Option<NormalFormat>` para normal-format); `apply_overrides(&mut params, &cli)` após `preset.params()` (ou após auto-resolve).
**`--ao-mode` REMOVIDO** (B4: Q2 fora de escopo).
**Aceitação:** `--height-contrast 3.0` produz height_map com `luma_std` ≥ 1.5× do default no mesmo input.

### F3.3 — Selective maps (`--only`, `--skip`)
- `--only height,normal` whitelist; `--skip edge,ao` blacklist. Mutuamente exclusivos (clap `conflicts_with`).
- Maps válidos: `height, normal, metallic, smoothness, edge, ao, curvature`.
- `Pipeline::process` aceita `MapSelection` struct; só cria textures/dispatch/readback dos selecionados (economia de GPU).
**Aceitação:** `--only height,normal` gera só 2 arquivos; `--skip edge,ao` gera 4.

### F3.4 — Verbose timing + adapter info
- Em `-v`: print adapter name (`adapter.get_info().name`), backend, VRAM disponível (`adapter.limits().max_storage_buffers_per_shader_stage` indireto; VRAM real não exposto por wgpu — omitir ou estimar).
- Timing por estágio (de F1.8).
- Total time + output sizes.
**Aceitação:** `-v` printa adapter name contendo string esperada (ex: "NVIDIA" ou "Intel" em máquina de teste, ou skip se CI headless).

### F3.5 — Presets extras (12 novos)
Adicionar a `preset.rs`: `Concrete, Leather, Marble, Sand, Foliage, Plaster, Asphalt, Brick, Ice, Snow, Lava, Water`.
Cada um com `PresetParams` tuned (valores calibrated por teste smoke). Atualizar `FromStr`, `Display`, `ALL` (sem `Auto`), help text, docs.
**Aceitação:** `cargo run -- --list-presets` printa 19 entradas; roundtrip string teste passa para todos.

### F3.6 — `materialize info <image>` (subcomando), `--list-presets`, `--list-maps`
**Decisão (resolve m4):** `info` vira **subcomando** (não flag), evita conflito com `input: Option<String>` posicional.
- `materialize info <image>`: roda `analyze::analyze` + `classify`, print features + preset sugerido + tile status + alpha, **sem gerar**. Exit 0.
- `--list-presets` e `--list-maps` (flags): curto-circuitam em `main.rs` **antes** do check de input (`main.rs:28`). Print tabelas, exit 0.
**Aceitação:** `materialize info tests/fixtures/wood.png` printa `wood` em algum lugar; `--list-presets` exit 0 sem input.

### F3.7 — Docs
- `README.md`, `README_PT.md`, `AGENTS.md`, `docs/cli-api.md` (corrigir completions syntax, exit codes, env vars, novos flags), `docs/features.md`, `docs/algorithms.md` (auto-detection section), `docs/shaders.md` (struct Params novo, wrap sampling), `docs/roadmap.md` (marcar entregue, remover future items done).
- `CHANGELOG.md`: seção `[2.0.0] - 2026-06-XX` com todas as mudanças (incluindo "Breaking changes em 2.0" list BC1-BC9).
- `Cargo.toml`: `version = "2.0.0"`.

---

## F4 — Qualidade de maps

### F4.1 — Smoothness espacial (resolves m-B3)
**Bug atual (`smoothness.wgsl:38`):** `smoothness = base + boost*metallic` — ignora variação espacial.
**Fix:**
```wgsl
fn local_contrast_5x5(center: vec2<i32>, dims: vec2<u32>) -> f32 {
    var sum = 0.0; var n = 0.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            let c = sample_coord(center + vec2<i32>(dx, dy), dims);
            let luma = dot(textureLoad(diffuse_texture, c, 0).rgb, vec3(0.2126, 0.7152, 0.0722));
            sum += luma; n += 1.0;
        }
    }
    let mean = sum / n;
    var var = 0.0;
    for (var dy = -2; dy <= 2; dy++) {
        for (var dx = -2; dx <= 2; dx++) {
            let c = sample_coord(center + vec2<i32>(dx, dy), dims);
            let luma = dot(textureLoad(diffuse_texture, c, 0).rgb, vec3(0.2126, 0.7152, 0.0722));
            var += (luma - mean) * (luma - mean);
        }
    }
    return clamp((var / n) * 8.0, 0.0, 1.0);
}
// ...
let lc = local_contrast_5x5(coords, dims);
let smoothness = clamp(
    params.smoothness_base
        + params.smoothness_metallic_boost * metallic
        - params.smoothness_roughness_factor * lc,
    0.0, 1.0
);
```
**Intuição:** áreas de alto contraste local (textura visível) = ásperas = smoothness reduzido.
**Aceitação:** fixture `concrete.png` (áspero) produz smoothness_mean ≤ 0.6× o de `metal_polished.png`.

### F4.2 — Curvature map (OPT-IN, resolves M8)
- 7º output **opt-in** via `--include-curvature`.
- Shader `src/shaders/curvature.wgsl`: Laplaciano do height:
  ```wgsl
  let h = textureLoad(height_texture, sample_coord(coords, dims), 0).r;
  let hl = ...; let hr = ...; let ht = ...; let hb = ...;
  let laplacian = (hl + hr + ht + hb) - 4.0 * h;
  let curvature = clamp(laplacian * 8.0 + 0.5, 0.0, 1.0);  // 0.5 = flat, >0.5 concave, <0.5 convex
  textureStore(output_texture, coords, vec4(curvature, curvature, curvature, 1.0));
  ```
- Ripple: `PbrMaps.curvature: Option<Vec<u8>>`, `OutputPaths.curvature_path: Option<String>`, `get_output_paths(..., include_curvature)`, `curvature_to_image`, `Pipeline::process(..., map_selection)` condicional dispatch, `main.rs` save condicional.
**Aceitação:** `--include-curvature` gera 7º arquivo; sem flag = 6 arquivos (compat).

### F4.3 — Roughness output
- `--roughness` (flag): gera `texture_roughness.png` (em vez de `_smoothness.png`) com `1 - smoothness`. Mútuo exclusivo com smoothness? Não — default continua smoothness; `--roughness` adicional, ou substitui?
- **Decisão:** `--roughness` substitui smoothness por roughness (1 output). Para ambos, usar `--smoothness --roughness` (gera 2).
**Aceitação:** `--roughness` gera `texture_roughness.png` (sem `_smoothness.png`); `--smoothness --roughness` gera ambos.

### F4.4 — Normal flip-Y (resolves m-B3)
- `--normal-format <opengl|directx>` (default `opengl`).
- Implementado como `params.normal_flip_y` (u32, 0/1) já no struct (F0.1).
- `normal.wgsl`: após calcular `gy`, `if (params.normal_flip_y == 1u) { gy = -gy; }`.
**Aceitação:** fixture com gradiente vertical conhecido: `directx` produz normal_map green channel invertido vs `opengl`.

### F4.5 — Metallic expandido (redesenhado, resolves M2)
**Problema original:** 4/6 "novos" metais eram subset do gray-metal rule (Titanium/Pewter/Chrome/Blue-steel); Bronze/Brass overlap com Copper/Gold. Redesenho em **tiers explícitos sem overlap**:
```wgsl
fn detect_metallic(rgb: vec3<f32>) -> f32 {
    let hsl = rgb_to_hsl(rgb);
    let h = hsl.x; let s = hsl.y; let l = hsl.z;
    var metallic = 0.0;

    // === Achromatic metals (sat < 0.15) — cobre steel/silver/aluminum/titanium/pewter/chrome/blue-steel como UM grupo
    if (s < 0.15 && l > 0.30 && l < 0.92) {
        let lum_factor = smoothstep(0.30, 0.85, l);
        let sat_factor = 1.0 - smoothstep(0.0, 0.15, s);
        // Blue tint bonus (titanium blue / blue steel)
        let blue_factor = select(1.0, 1.15, h > 0.55 && h < 0.68);
        metallic = max(metallic, clamp(lum_factor * sat_factor * blue_factor, 0.0, 1.0));
    }

    // === Chromatic metals (sat >= 0.30), hue bands NÃO-OVERLAPPING
    if (s >= 0.30 && l > 0.20) {
        var chromatic = 0.0;
        // Copper: hue [0.00, 0.06)
        if (h >= 0.00 && h < 0.06) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.03) * 16.0);
        }
        // Bronze: hue [0.06, 0.09)  — gap explícito do cobre/ouro
        else if (h >= 0.06 && h < 0.09) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.075) * 33.0);
        }
        // Gold: hue [0.09, 0.14)
        else if (h >= 0.09 && h < 0.14) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.115) * 22.0);
        }
        // Brass: hue [0.14, 0.17)
        else if (h >= 0.14 && h < 0.17) {
            chromatic = max(chromatic, 1.0 - abs(h - 0.155) * 33.0);
        }
        if (chromatic > 0.0) {
            let lum_factor = smoothstep(0.20, 0.70, l);
            let sat_factor = smoothstep(0.30, 0.80, s);
            metallic = max(metallic, clamp(chromatic * lum_factor * sat_factor, 0.0, 1.0));
        }
    }
    return clamp(metallic, 0.0, 1.0);
}
```
**Mudança:** achromatic vira 1 grupo (sem subset inútil); chromatic usa 4 bands **não-overlapping** com gaps explícitos. Não há mais subsumição.
**Aceitação:** corpus de metais: `copper.png, bronze.png, gold.png, brass.png, steel.png, titanium.png` → cada um produz `metallic_mean > 0.5`; `concrete.png` e `wood.png` → `< 0.1`.

---

## Decomposição para execução (Sprint 0 OBRIGATÓRIO primeiro)

### Sprint 0 — PREREQUISITO (serializado)
- **F0.1:** Bump `PresetParams` 48→64 + atualizar `Params` struct nos 6 shaders existentes + size test. Commit standalone verde.

### Sprint 1 — Bloco A (paralelizável, shaders independentes após Sprint 0)
- A1: F1.1 (edge rewrite) + teste regressão.
- A2: F4.5 (metallic tiers) + F2.5 (local variance damping) + F1.6 (hardening) — mesmo arquivo.
- A3: F4.1 (smoothness espacial).
- A4: F4.4 (normal flip-Y).
- A5: F4.2 (curvature shader + io + pipeline opt-in).

### Sprint 2 — Bloco B (paralelizável, CLI infra)
- B1: F1.2 (exit codes enum + error.rs).
- B2: F1.3 (env vars).
- B3: F1.4 (completions).
- B4: F1.8 (timing) + F3.4 (adapter info verbose).
- B5: F3.2 (inline overrides) + F3.3 (selective maps).
- B6: F3.5 (12 presets extras).
- B7: F3.6 (info subcommand + list flags).

### Sprint 3 — Bloco C (auto-detecção, depende de fixtures corpus)
- C1: Criar `tests/fixtures/classification/*.png` (≥19, idealmente 57) com `expected_preset.txt`.
- C2: F2.1 (analyze.rs features) + testes.
- C3: F2.2 (classify + calibrar thresholds no corpus).
- C4: F2.3 (-p auto integração).
- C5: F2.4 (auto-tile wrap sampling em 4 shaders).

### Sprint 4 — Bloco D (batch + integration)
- D1: F3.1 (batch dir/glob + GPU thread dedicada).
- D2: Integração `main.rs` final (info subcommand bypass input check, list flags bypass, batch chama Pipeline reusado).
- D3: F1.5 (doc-truth formatos).

### Sprint 5 — Bloco E (docs)
- E1: F3.7 (todos docs + CHANGELOG + Cargo.toml bump 2.0.0).
- E2: Atualizar `.cursor/skills/materialize-cli/SKILL.md` se necessário.

---

## Critérios de aceitação (automatizados, resolves M9)

| Item | Critério objetivo |
|---|---|
| Build | `cargo build --release` sem warnings; `cargo clippy -- -D warnings` limpo |
| F0.1 | `cargo test test_preset_params_size` passa; size = 64 |
| F1.1 | Edge regression: borda vertical → pixel max > 200; plana → < 30 |
| F1.2 | Exit code test parametrizado: nonexistent→2, corrupt→3, gpu-fail-mock→4, forbidden-write→5, oversized-mock→6 |
| F1.3 | `MATERIALIZE_GPU_BACKEND=invalid` → exit 4 com msg; `MATERIALIZE_LOG=debug` produz stderr |
| F1.4 | `--generate-completions bash` exit 0 com stdout não-vazio contendo "materialize" |
| F1.8/F3.4 | `-v` contém `ms)` por estágio + adapter name |
| F2.1 | analyze em fixtures conhecidos retorna features esperadas (branco→luma=1, gradiente→edge_density>0.3) |
| F2.2 | classify acerta ≥90% do corpus classification |
| F2.3 | `-p auto` printa preset; comparação pixels < 5% diff vs preset explícito |
| F2.4 | tileable input → mapas sem seam (MSE wrap vs clamp < 1%) |
| F2.5 | metal cinza liso → metallic_mean ≥ 5× cimento texturizado |
| F3.1 | Batch processa N imagens; `--skip-existing` pula; exit reflete failures |
| F3.2 | `--height-contrast 3.0` → luma_std ≥ 1.5× default |
| F3.3 | `--only height,normal` gera 2 arquivos; `--skip edge,ao` gera 4 |
| F3.5 | `--list-presets` printa 19; roundtrip string passa |
| F3.6 | `materialize info <img>` printa preset detectado; `--list-presets` exit 0 sem input |
| F4.1 | concrete smoothness_mean ≤ 0.6× metal_polished |
| F4.2 | `--include-curvature` gera 7º arquivo; sem flag = 6 |
| F4.3 | `--roughness` gera `_roughness.png` ao invés de `_smoothness.png` |
| F4.4 | directx inverte green channel vs opengl no mesmo input |
| F4.5 | corpus metais → todos metallic_mean > 0.5; não-metais < 0.1 |
| Docs | diff docs reflete realidade; CHANGELOG 2.0.0 com BC1-BC9; Cargo.toml 2.0.0 |

---

## Riscos (atualizados pós-Momus)

- **WGSL storage R8Unorm write** backend-restrito → manter Rgba8Unorm com 1 canal.
- **Novas deps** (`clap_complete`, `env_logger`, opcional `indicatif`, `rayon`) aumentam build time levemente.
- **GPU thread dedicada** (`F3.1`) — wgpu Device/Queue não podem ser compartilhados entre threads para dispatch concorrente; canal+single-owner é o padrão seguro.
- **Auto-tile wrap sampling** — pode dar artefatos em texturas quase-tileable; threshold `tile_mse < 0.005` calibrado em corpus; override manual via `--seamless`/`--no-seamless`.
- **Curvature opt-in** — decisão BC7 mantém output set default em 6 maps (compat).
- **Breaking 2.0** — smoothness default preservado; roughness/curvature opt-in; só exit codes + struct size mudam internamente.
- **tokio + blocking `device.poll`** — `Pipeline::process` roda no GPU thread dedicado (não no runtime tokio), evitando starvation do runtime. Documentar no `batch.rs`.
- **Calibração de thresholds F2.2** — se corpus < 19 amostras, thresholds podem ser instáveis; smoke test de regressão falha se classify cair abaixo de 90%.
- **`Preset::Auto` em `ALL`** — excluir para não quebrar `test_preset_roundtrip_str` (Auto não tem params fixos).

---

## Métricas

- **LOC adicionado:** ~2000-2500.
- **Novos arquivos:** `src/error.rs`, `src/analyze.rs`, `src/batch.rs`, `src/shaders/curvature.wgsl`, `tests/fixtures/classification/` (corpus), `tests/test_analyze.rs`.
- **Novos deps:** `clap_complete`, `env_logger` (ou `tracing`), `indicatif` (opcional), `rayon`.
- **Tempo estimado:** 5-6 dias focado, decomposto em ~30 sub-tarefas delegáveis.
- **Sprints:** 0 (prereq) → 1 (shaders) → 2 (CLI) → 3 (auto) → 4 (batch) → 5 (docs). Sprints 1 e 2 podem parcialmente sobrepor-se em paralelo após Sprint 0.

---

## Changelog pós-Momus (mudanças v1→v2 do plano)

- B1 (sequencing): Sprint 0 novo com `PresetParams` refactor ANTES dos shaders.
- B2 (F1.7): dropado, era falso bug.
- B3 (struct size): explicitado 48→64 + sync 6 shaders + size test.
- B4 (ao-mode): removido do F3.2.
- M1 (auto corpus): classificação corpus + test ≥90%.
- M2 (F4.5 redesign): tiers não-overlapping em vez de 6 metais redundantes.
- M3 (GPU concurrency): thread dedicada via canal; `--jobs` paraleliza CPU só.
- M4 (thresholds): calibração via corpus.
- M5 (F2.5): fórmula concreta.
- M6 (F1.6): reclassificado hardening, não bug.
- M7 (breaking): lista BC1-BC9 completa.
- M8 (F4.2): curvature opt-in decisão.
- M9 (criteria): tabela objetiva por item.
- m1 (WGSL select i32/u32): corrigido com cast `vec2<i32>(dims)`.
- m2 (sampling shaders): identificou 4 shaders + 2 padrões diferentes.
- m3 (Auto/ALL): excluir Auto de `ALL`.
- m4 (info flag/subcommand): virou subcomando.
- m5 (tile_mse bordas): amostragem completa de bordas.
- m6 (predicados vagos): thresholds numéricos explícitos.
- m7 (tokio blocking): GPU thread dedicada documenta.
