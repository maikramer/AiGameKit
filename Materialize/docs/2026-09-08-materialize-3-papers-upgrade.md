# Materialize 3.0 — Aprimoramento das técnicas com métodos modernos (papers)

**Data:** 2026-09-08 · **Estado:** implementado (Fases 0–7) · **Sucessor do:** `roadmap.md` v2.1/v3.1/v4.0 · **Algoritmos atuais:** [`algorithms.md`](algorithms.md)

Este documento é o contrato de projeto do upgrade algorítmico do `materialize-cli`.
Cada fase descreve: o problema atual, o método novo (com referência ao paper),
as mudanças concretas de código/CLI/params, e os critérios de aceitação.

---

## 1. Motivação e diagnóstico

O Materialize 2.0 gera 6–7 mapas PBR a partir do diffuse com passes wgpu simples e
heurísticos. O diagnóstico por pass (estado 2.0):

| Pass | Algoritmo 2.0 | Problema |
|------|---------------|----------|
| Height | 3 box blurs (janela quadrada) + contraste | Box blur não é Gaussiano (docs prometiam pirâmide σ=1..64 que não existe); sem separação base/detail — sombras/escuridão macro viram "relevo" |
| Normal | Sobel 3×3, z fixo = 1 | Sobel tem anisotropia direcional; z fixo impede escala física de derivada; sem pré-suavização (aliasing) |
| Metallic | Detector HSL por faixas de hue + damping + veto vegetação | Sensível à temperatura de cor da luz (sem white-balance); sem evidência especular |
| Smoothness | Variância de luminância 5×5 | Sem ligação física com microfacetas; ignora a informação do normal map |
| Edge | Gradiente central do normal R/G — **bug: magnitude usa só 2 de 4 componentes** (`edge.wgsl:59`) | Arestas diagonais subdetetadas |
| AO | 16 taps fixos em 8 direções × 2 distâncias | Sem horizonte real, sem falloff por distância, sem multi-escala |
| Curvature | Laplaciano 4-vizinho | Sensível a ruído; escala única |
| Tileability | Apenas wrap sampling (`seamless=1`) | Não existe *make-seamless*: uma textura não-tileable continua não-tileable |

Além disso: o struct `Params` (64 B) está duplicado à mão em 7 shaders WGSL,
não há **nenhum teste de algoritmo** (nem CPU nem GPU), e `--jobs` é um no-op.

## 2. Métodos modernos adotados (papers)

| Tema | Método | Referência |
|------|--------|-----------|
| Separação base/detail edge-aware | **Guided Filter** (O(1)/taps, preserva bordas) | He, Sun, Tang — *Guided Image Filtering*, ECCV 2010 / TPAMI 2013 |
| Suavização multi-escala do height | **Pirâmide Gaussiana separável** (σk = σ0·2^k) | Burt & Adelson — *The Laplacian Pyramid*, ACM TOG 1983 |
| Operador de gradiente | **Scharr** (invariância rotacional ótima 3×3) | Scharr — *Optimal Operators in Digital Image Processing*, 2000 |
| AO 2D | **Horizon-Based AO** adaptado a heightfield (máximo de horizonte por direção, falloff cos) | Bavoil & Saineel / Sander — *Image-Space Horizon-Based Ambient Occlusion*, NVIDIA SIGGRAPH 2008 |
| Tileability | **Offset mínimo por FFT** (SSD da banda de borda via correlação-cruzada exata) + cross-fade / **Poisson blending** | Kwatra et al. — *Graphcut Textures*, SIGGRAPH 2003 (offset+cut) · Pérez et al. — *Poisson Image Editing*, SIGGRAPH 2003 |
| Roughness física | Proxy de microfacet: variância de slope do normal → α GGX (α² = E[slope²]) | Walter et al. — *Microfacet Models (GGX)*, EGSR 2007 |
| Metallic robusto | **Gray-world white balance** pré-detecção + evidência especular | Buchsbaum — *A Spatial Processor Model for Object Colour Perception*, 1980 |
| Albedo intrínseco (opcional, ML) | Decomposição albedo/shading/especular por ordinal shading | Careaga & Aksoy — *Intrinsic Image Decomposition via Ordinal Shading*, TOG/SIGGRAPH Asia 2023 (repo `compphoto/Intrinsic`, MIT) · follow-up *Colorful Diffuse Intrinsic Image Decomposition in the Wild*, TOG 2024 |

## 3. Arquitetura 3.0

### 3.1 Pipeline multi-passe com ping-pong

O pipeline deixa de ser "1 shader = 1 mapa" e passa a ser uma **cadeia de
dispatches** num único command encoder (o WebGPU serializa dispatches dentro do
mesmo submit com barreiras implícitas):

```
diffuse ──▶ [make-seamless (F4, opcional)] ──▶ diffuse'
diffuse' ─▶ luma ─▶ pirâmide Gaussiana (σk, H+V por nível) ─▶ blend ponderado
        ─▶ guided filter (box H/V de I e I², aplicação) ─▶ base, detail
        ─▶ height = contraste(base) + detail_mix·detail
normal  = gradiente(base+detail) [Sobel|Scharr, σ pre-filter, slope_z]
edge    = variação total do normal (magnitude completa)
ao      = HBAO(height) ⊕ ao_macro_mix·HBAO(base, raio×4)
curv    = LoG multi-escala (níveis × σ 2^k)
metallic= detector HSL em diffuse gray-world (+ specular residual se F6)
smooth  = base + boost·metallic − rf·lc − slope_mix·slope_var_GGX (lê normal)
```

Texturas auxiliares R32Float reutilizadas por ping-pong (pool em `pipeline.rs`).

### 3.2 Uniform `PresetParams` — 160 bytes (40 slots)

O struct deixa de ser duplicado por shader: `shaders/params.wgsl` é o **único**
ficheiro com o struct, concatenado em runtime (`include_str!` + `format!`) à
frente do corpo de cada shader. A sincronização Rust↔WGSL continua guardada por
`test_preset_params_size` (= 160).

Novos campos por fase (todos f32/u32, layout `#[repr(C)]`):

| Campo | Fase | Default | Significado |
|-------|------|---------|-------------|
| `height_sigma_base` | 1 | 1.0 | σ0 da pirâmide |
| `height_pyramid_levels` | 1 | 4 | níveis 1..7 (σk = σ0·2^k) |
| `guided_radius` | 1 | 8 | raio do guided filter (px) |
| `guided_eps` | 1 | 0.01 | ε de variância (edge-preserving) |
| `detail_mix` | 1 | 0.8 | peso do detail no height final |
| `normal_operator` | 2 | 0 | 0=Sobel, 1=Scharr |
| `normal_slope_z` | 2 | 1.0 | escala z da derivada (texels de altura) |
| `normal_prefilter_sigma` | 2 | 0.5 | σ do blur pré-gradiente (0=off) |
| `curvature_levels` | 2 | 2 | níveis LoG (1..3) |
| `curvature_gain` | 2 | 8.0 | ganho (8 = compat 2.0) |
| `ao_directions` | 3 | 16 | direções do ray marching (8/16/32) |
| `ao_steps` | 3 | 12 | passos geométricos por direção |
| `ao_radius` | 3 | 16.0 | raio máximo px |
| `ao_macro_mix` | 3 | 0.30 | blend AO macro (base) |
| `seam_band` | 4 | 24.0 | largura da banda de blend (px) |
| `roughness_slope_scale` | 5 | 1.5 | ganho do proxy GGX |
| `roughness_slope_mix` | 5 | 0.25 | 0 desliga (compat 2.0) |
| `metallic_gray_world` | 5 | 1 | gray-world pré-detecção |
| `gray_r/g/b_gain` | 5 | 1.0 | ganhos CPU por imagem |
| `metallic_specular_gain` | 5 | 0.3 | evidência especular (0=off) |
| `shading_height_mix` | 6 | 0.0 | high-freq do shading → height |
| `filter_radius/sigma/kind` | — | — | params do passe de blur genérico (H/V) |

### 3.3 Testes (golden CPU + paridade GPU)

- `src/reference.rs`: implementações de referência **CPU** (Rust puro) de todos
  os passes — rodam em CI sem GPU. Fixtures sintéticas com ground truth
  analítico (rampa, impulso Gaussiano, degrau, seno, checker).
- `tests/gpu_parity.rs`: paridade CPU↔GPU dentro de tolerância (1/255 por
  canal u8, 1e-3 para f32); só corre com `MATERIALIZE_GPU_TESTS=1` ou GPU
  detetada (CI não tem GPU — fica para o runner local).
- Floor do monorepo: manter **≥100 casos** no crate (hoje ~180; +~50 novos).

## 4. Fases

### Fase 0 — Higiene e infra de testes

1. **Fix do bug do edge**: magnitude completa
   `sqrt(|gx|² + |gy|²)` com `gx/gy` vetores 2D (usa os 4 componentes).
2. **Dedup do `Params`**: `shaders/params.wgsl` único (struct + `sample_coord`
   + helpers comuns), concatenado em runtime. Corpos WGSL perdem o struct.
3. **`src/reference.rs`** + testes golden CPU de todos os passes existentes.
4. **`tests/gpu_parity.rs`** com gate de GPU.
5. `adapter_info_string` real (`adapter.get_info()` — o comentário "wgpu hides
   get_info" está desatualizado).
6. Remover `--jobs` (no-op que mente para o utilizador).
7. Reescrever `docs/algorithms.md` para refletir o código.

**Aceitação:** `cargo test` verde (CPU), paridade GPU verde local, docs coerentes.

### Fase 1 — Height: pirâmide Gaussiana + guided filter

Substitui os 3 box blurs:

1. **Luma** (Rec.709) em R32Float.
2. **Pirâmide Gaussiana sem downsample**: para k = 0..L−1, blur separável
   (H depois V) com σk = σ0·2^k, raio = ⌈3σ⌉; blend ponderado
   `h = Σ wk·G_k(luma)` com pesos geométricos normalizados (w0 maior).
   Kernels Gaussianos verdadeiros (não box).
3. **Guided filter** (He 2010), self-guidance sobre h:
   - `meanI = box_r(h)`, `meanII = box_r(h²)` (blur separável H+V de raio r)
   - `varI = meanII − meanI²`
   - `base = meanI + varI/(varI+ε)·(h − meanI)` (preserva bordas; ε controla)
   - `detail = h − base`
4. **Height final** = `clamp(contraste(base) + detail_mix·detail)`; o pipeline
   expõe internamente `height` (final), `base` e `detail` (texturas R32Float)
   para os passes a jusante.

**Aceitação:** degrau de luminância não vira rampa no base (edge-preserving,
teste golden); height de um gradiente suave mantém monotonicidade; paridade GPU.

### Fase 2 — Normal, curvature, edge

1. **Normal**: operador Sobel|Scharr (`normal_operator`); Scharr normalizado
   ÷4 para manter a magnitude dos presets (Scharr/Sobel = 4× em resposta);
   `normal_slope_z` como componente z do vetor normal (escala física);
   `normal_prefilter_sigma` (blur Gaussiano do height antes do gradiente).
2. **Curvature**: LoG multi-escala — por nível l ∈ 0..L: Laplaciano 4-vizinho
   do height com blur σ=2^l; combinação `max` sobre níveis; ganho
   `curvature_gain` (compat: 1 nível + ganho 8 ≈ 2.0).
3. **Edge**: variação total do normal (magnitude vetorial completa, fix F0) +
   `smoothstep` como hoje.

**Aceitação:** normal de rampa = constante com direção correta (golden);
curvature de seno cruza 0.5 nos inflexões; edge deteta diagonal 45° (o 2.0 não detetava).

### Fase 3 — AO horizon-based multi-scale (HBAO 2D)

Para cada pixel, para cada uma de `ao_directions` direções uniformes:

```
horizon = max_{s=1..ao_steps} atan2( (h(p+d·s) − h(p))·ao_depth_scale , |d·s| )
occ_dir = sin(clamp(horizon, 0, π/2)) · falloff(dist)     // peso cos (HBAO)
ao_fine = 1 − mean_dir(occ_dir)
ao_macro = idem sobre `base` com raio ×4
ao = mix(ao_fine, ao_macro, ao_macro_mix)
```

Passos geométricos (`s_i = i²/ao_steps·raio`) cobrem perto+longe com poucas
amostras. `--ao-quality fast|medium|high` mapeia para (8,8)/(16,12)/(32,24).

**Aceitação:** parede (degrau) → escuro na base do lado côncavo e claro no
topo convexo (golden analítico); parede perfeitamente plana → AO=1.

### Fase 4 — Make-seamless (tileability)

`--make-seamless fast|high` (default off; auto-tile deteta e salta se já tileable):

1. **Offset de custo mínimo (CPU)** — a contribuição do Graphcut Textures
   (Kwatra 2003) é o *cut* de fronteira com erro mínimo. Para seams retos o
   problema é **separável**: o seam vertical da imagem rolada corta as colunas
   (dx−1, dx) do original, logo o custo é `V(dx) = Σ_y |I(dx,y) − I(dx−1,y)|`;
   o horizontal é `H(dy) = Σ_x |I(x,dy) − I(x,dy−1)|`. Duas passadas O(W·H)
   produzem todos os custos; os argmins (excluindo shifts triviais |d| < w/8)
   dão o offset. *(Nota de implementação: a versão FFT do doc original avaliava
   o mesmo critério via correlação-cruzada; o scan direto separável é
   equivalente, O(W·H) e sem dependência nova.)*
2. **Blend do seam (compute, `seamless.wgsl`)**:
   - `fast`: cross-fade mirror-pair — cada pixel da banda funde com o seu
     espelho através do seam (x ↔ W−1−x), peso 0.5 na borda (os dois extremos
     do seam convergem para o mesmo valor ⇒ C0 exato no wrap) decaindo a 0 no
     fim da banda. Aplicado ao diffuse **antes** da cadeia ⇒ todos os mapas
     herdam o seam alinhado (luma/blur/contraste comutam com o blend).
   - `high`: Poisson blending (Pérez 2003) na banda — Jacobi em compute (48
     iterações, ping-pong rgba16float), Δf = Δ(rolled) dentro da banda, f = T
     (fast-blend) fora — remove o ghosting do blend linear mantendo a
     continuidade.
3. Auto-tile existente (`tile_mse`) continua: se a textura já é tileable,
   make-seamless é no-op (apenas wrap sampling).

**Aceitação:** (verificado) faixa na borda → roll põe ambas as colunas de
borda em fundo (`border_mse` cai >40%); blend mirror-pair dá igualdade luma
coluna 0 = coluna W−1 ao longo do vão das arestas (cantos = compromisso 2D,
residual já minimizado pelo roll).

### Fase 5 — Roughness/metallic física

1. **Roughness por variância de slope (GGX)**: no shader de smoothness (que
   passa a ler também o normal map): `slope_var = var5×5(nx) + var5×5(ny)`;
   proxy `α ≈ slope_scale·√slope_var` → contributo de roughness
   `smoothness −= roughness_slope_mix·min(1, α)`. Fisicamente: microfacetas
   dispersas ⇒ normal ruidoso ⇒ roughness alta (Beckmann/GGX: α² = E[slope²]).
2. **Metallic gray-world**: CPU calcula ganhos de canal (luma_mean/c_mean) e
   os passa em `gray_r/g/b_gain`; o shader aplica antes do detector HSL
   (elimina falsos negativos/positivos por temperatura de luz).
3. **Evidência especular**: `spec = smoothstep(0.75,0.95,l)·(1−s)` (luma alto
   dessaturado); com F6 ativo, lê o **specular residual** direto.
   `metallic += specular_gain·spec·(1 − damping)` apenas onde o hue é plausível.

**Aceitação:** ouro sob luz azulada deteta igual sob luz branca (golden);
metal polido (baixa slope_var, alto luma) → smoothness alto; metal escovado
(alta slope_var) → smoothness baixo.

### Fase 6 — Albedo/basecolor intrínseco (ML opcional via vramd)

1. **Backend vramd `intrinsic`**: worker Python (JSONL stdin/stdout, o padrão
   dos outros backends) em venv próprio, registado em `backends.yaml`,
   embrulhando o modelo de `compphoto/Intrinsic` (MIT) — Careaga & Aksoy 2023.
   Input: caminho da imagem; output: `{albedo, shading, specular}` (PNGs).
   Edição de código do worker → `vramd respawn intrinsic`.
2. **CLI**: `materialize decompose <img> -o <dir>` (delega via vramd; erro
   limpo se o backend/modelo não estiver instalado) e `--intrinsic` no
   generate (auto-decompõe e consome).
3. **Consumo no pipeline**:
   - metallic/smoothness leem o **albedo** (sem shading ⇒ menos falsos positivos);
   - specular residual alimenta a evidência metálica e o boost de smoothness;
   - high-freq do shading entra no height: `height += shading_height_mix·
     hp_filter(shading)` (shape-from-shading fino; default 0 = off).
4. **Degradação graciosa**: sem vramd/backend → warning e pipeline 100%
   heurístico (zero dependência do ML no crate Rust).

**Aceitação:** com backend: albedo sem sombras queimadas (specular removido);
sem backend: output idêntico ao caminho heurístico + 1 warning.

### Fase 7 — Integração GameAssets + validação final

1. `GameAssets` `_materialize_diffuse_argv` passa `--preset auto` +
   `--roughness` + `--make-seamless fast` para categorias tiling (floor/wall/
   ground/roof); flags de perfil novas: `materialize_preset`,
   `materialize_make_seamless`, `materialize_ao_quality`, `materialize_intrinsic`.
2. Docs: `README.md`/`README_PT.md` do crate, `docs/algorithms.md` com o mapa
   paper→pass, secção no `AGENTS.md` do monorepo se necessário.
3. `make check` completo + floor de testes ≥100 mantido.

---

## 5. Matriz de params por preset (novos campos)

| Preset | σ0 | níveis | guided_r | detail_mix | op | slope_z | ao dirs/steps | macro_mix | slope_mix |
|--------|----|--------|----------|-----------|----|---------|---------------|-----------|-----------|
| default | 1.0 | 4 | 8 | 0.8 | sobel | 1.0 | 16/12 | 0.30 | 0.25 |
| skin | 1.5 | 4 | 10 | 0.5 | sobel | 1.2 | 12/10 | 0.25 | 0.15 |
| floor | 0.8 | 4 | 6 | 0.9 | sobel | 0.9 | 16/16 | 0.40 | 0.30 |
| metal | 1.0 | 3 | 8 | 0.7 | **scharr** | 0.8 | 16/12 | 0.20 | 0.40 |
| stone | 0.7 | 3 | 5 | 0.95 | sobel | 0.8 | 24/16 | 0.45 | 0.30 |
| fabric | 1.0 | 4 | 6 | 0.9 | sobel | 1.1 | 12/10 | 0.35 | 0.35 |
| … | | | | | | | | | |

(valores completos no `preset.rs`; presets existentes mantêm os campos 2.0
equivalentes — normal_strength, height_contrast, etc.)

## 6. Riscos e mitigações

| Risco | Mitigação |
|-------|-----------|
| Guided filter escurece/clareia base global | contraste aplicado só ao base com pivot 0.5 como hoje; ε por preset |
| Pirâmide σ=64 custa caro em texturas 4K | cap de níveis por preset (≤5); kernels separáveis; um submit único |
| FFT em 4K = memória (pow2 padding) | FFT só no caminho make-seamless; f32 complex; cap 4096 |
| Scharr muda visual dos presets existentes | default continua Sobel; Scharr normalizado ÷4 |
| Modelo intrinsic: VRAM/dependências | backend vramd opcional em venv isolado; degradação graciosa |
| Regressão silenciosa de qualidade | golden CPU + paridade GPU por pass; comparador A/B (`aigamekit-lab` style) antes/depois nos presets |

## 7. Referências

- He, Sun, Tang. *Guided Image Filtering*. ECCV 2010 / TPAMI 2013. https://people.csail.mit.edu/kaiming/eccv10/index.html
- Burt, Adelson. *The Laplacian Pyramid as a Compact Image Code*. ACM TOG 1983.
- Scharr. *Optimal Operators in Digital Image Processing* (dissertação), 2000.
- Bavoil, Sander. *Image-Space Horizon-Based Ambient Occlusion*. NVIDIA, SIGGRAPH 2008 Talks.
- Kwatra et al. *Graphcut Textures: Image and Video Synthesis Using Graph Cuts*. SIGGRAPH 2003.
- Pérez, Gangnet, Blake. *Poisson Image Editing*. SIGGRAPH 2003.
- Walter, Marschner, Li, Torrance. *Microfacet Models for Refraction through Rough Surfaces*. EGSR 2007 (GGX).
- Buchsbaum. *A Spatial Processor Model for Object Colour Perception*. 1980 (gray-world).
- Careaga, Aksoy. *Intrinsic Image Decomposition via Ordinal Shading*. ACM TOG (SIGGRAPH Asia) 2023. https://arxiv.org/abs/2311.12792 · https://github.com/compphoto/Intrinsic
- Careaga, Aksoy. *Colorful Diffuse Intrinsic Image Decomposition in the Wild*. ACM TOG 2024. https://arxiv.org/abs/2409.13690
- Rodriguez-Pardo et al. *SeamlessGAN: Self-Supervised Synthesis of Tileable Texture Maps*. 2022 (contexto de stacks tileable). https://arxiv.org/abs/2201.05120
