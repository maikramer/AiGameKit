# Algorithms — materialize-cli 3.0

Este documento descreve **o que o código faz hoje** (3.0). O mapa
paper→implementação vive aqui; o plano de upgrade com a motivação completa está
em [`2026-09-08-materialize-3-papers-upgrade.md`](2026-09-08-materialize-3-papers-upgrade.md).

Todas as passes são compute shaders wgpu (`@workgroup_size(8,8,1)`,
`textureLoad`, sem samplers). O struct `Params` (160 B) e `FilterParams`
(32 B) vivem **uma única vez** em `src/shaders/params.wgsl` e são concatenados
à frente de cada corpo de shader em runtime (`Pipeline::compose`). A cadeia
inteira de uma imagem corre num **único encoder/submit** — o WebGPU serializa
dispatches com barreiras implícitas entre passes.

## Height (F1) — pirâmide Gaussiana + guided filter

**Referências:** Burt & Adelson 1983 (pirâmide); He, Sun & Tang, *Guided Image
Filtering*, ECCV 2010/TPAMI 2013.

1. **Luma** Rec.709 do diffuse (`unary.wgsl:main_luma`) → R32Float.
2. **Pirâmide Gaussiana sem downsample** (`blur.wgsl`):
   - nível 0: blur separável σ0 (`height_sigma_base`);
   - nível k>0: **duas voltas** de blur σ0·√3 sobre o nível k−1 — como σ²
     somam, `σk = σ0·2^k` com kernels sempre pequenos (O(1) amostras/px/nível);
   - blend ponderado `h = Σ wk·nível_k` com `wk = 2(L−k)/(L(L+1))`
     (4 níveis → 0.4/0.3/0.2/0.1) via `unary:main_scale` + `binary:main_blend_add`.
3. **Guided filter** (self-guidance, `guided.wgsl`):
   - `meanI = box_r(h)`, `meanII = box_r(h²)` (duas passes separáveis box,
     raio `guided_radius`);
   - `varI = meanII − meanI²`; `base = meanI + varI/(varI+ε)·(h − meanI)`;
   - `detail = h − base`. ε pequeno (`guided_eps`) ⇒ preserva bordas fortes.
4. **Height final** (`height_final.wgsl`):
   `height = clamp((base − 0.5)·contrast + 0.5 + detail_mix·detail
   [+ shading_height_mix·hf_shading (F6)], 0, 1)`.

O pipeline expõe `height`, `base` e `detail` como texturas internas: AO macro
lê `base`; passes finos leem `height`.

## Normal (F2)

**Referência:** Scharr 2000 (operador ótimo 3×3).

- Pré-filtro Gaussiano inline (σ = `normal_prefilter_sigma`, separável no
  shader) sobre o height — mata aliasing antes do gradiente.
- Gradiente central com operador selecionável (`normal_operator`):
  - **Sobel** `[[1,2,1],[0,0,0],[-1,-2,-1]]` — resposta 8/degrau-unitário;
  - **Scharr** `[[3,10,3],…]` **÷4** — mesma escala de magnitude dos presets,
    melhor invariância rotacional.
- `n = normalize(−gx·strength, −gy·strength, slope_z)`; `slope_z`
  (`normal_slope_z`) é a componente z — escala física da derivada (texels de
  altura). Encode `*0.5+0.5` → RGBA8; `normal_flip_y` alterna OpenGL/DirectX.

## AO (F3) — horizon-based, multi-escala

**Referência:** Bavoil & Sander, *Image-Space Horizon-Based Ambient
Occlusion*, NVIDIA SIGGRAPH 2008 — adaptação 2D a heightfield.

Por pixel e por direção (`ao_directions` direções uniformes):

```
para s = 1..ao_steps (progressão geométrica: dist = raio·s²/steps²):
    h_s = height(pixel + dir·dist)
    horizonte = max(horizonte, atan((h_s − h_c)·ao_depth_scale / dist))
occlusão_dir = max(0, sin(horizonte))       // lobo cosseno do ambiente uniforme
```

- **Fine AO** sobre o height completo, raio `ao_radius`.
- **Macro AO** sobre o `base` do guided filter, raio ×4 — cavidades de larga
  escala sem o ruído fino.
- `ao = 1 − mix(occl_fine, occl_macro, ao_macro_mix)`.
- `--ao-quality fast|medium|high` = (8,8)/(16,12)/(32,24) direções×passos.

## Metallic (F5)

1. **Gray-world white balance** (Buchsbaum 1980): ganhos de canal
   `luma_mean/canal_mean` computados na CPU (`analyze::gray_world_gains`) e
   aplicados antes da detecção — as bandas de hue ficam estáveis sob luz
   colorida.
2. **Detector HSL em dois tiers** (igual ao 2.0): acromático (s<0.15,
   0.30<l<0.92, bónus azul-titânio) + cromático por bandas de hue
   não-sobrepostas (copper/bronze/gold/brass).
3. **Damping por variância** local de luminância 3×3 (textura não-metal).
4. **Veto de vegetação** (fração verde 9×9 via taps 5×5 stride 2).
5. **Evidência especular** (F5/F6): highlight claro+dessaturado
   (`smoothstep(0.75,0.95,l)·(1−s)`), substituída pelo **residual especular
   do passe intrínseco** quando disponível (textura de 2ª entrada).

## Smoothness/Roughness (F5)

**Referência:** Walter et al. 2007 (GGX: α² = E[slope²]).

`smoothness = base + boost·metallic − rf·contraste_luma_5×5 −
slope_mix·min(1, slope_scale·√var_5×5(nx,ny))`

O último termo é o proxy de microfacet: microfacetas dispersas ⇒ normal
ruidoso ⇒ roughness alta. `roughness_slope_mix=0` reproduz o 2.0. O export com
`--roughness` inverte (255−v) e renomeia o ficheiro.

## Edge/Crease (F0 fix)

Variação total do normal: gradiente central do campo 2D (R,G),
`mag = √(|gx|²+|gy|²)` com gx/gy **vetores completos** (o 2.0 usava só 2 dos
4 componentes — arestas a 45° escapavam). `smoothstep(0.05, 0.40,
mag·edge_contrast)`.

## Curvature (F2) — LoG multi-escala

Laplaciano 4-vizinho do height com blur Gaussiano σ ∈ {1, 2, 4}
(`curvature_levels` 1..3); combinação por **maior resposta assinada**
(`max |·|` preservando sinal). Convexo < 0.5 < côncavo; ganho
`curvature_gain` (8 = compat 2.0).

## Seamless / wrap (F2.4) e make-seamless (F4)

- `seamless=1` (flag/auto-tile): **wrap sampling** em todas as passes —
  saídas tileable se a entrada já for.
- `--make-seamless fast|high` (F4): pré-passe no diffuse antes da cadeia —
  ver secção própria no doc do plano. Todos os mapas herdam o seam alinhado
  (luma/blur/contraste comutam com o blend).

## Testes (golden + paridade)

- `src/reference.rs`: implementações CPU f32 de todas as passes (mesmos
  kernels, mesma codificação rgba8unorm com arredondamento, mesma truncagem
  WGSL f32→i32) — testes golden analíticos correm em CI sem GPU.
- `tests/gpu_parity.rs`: paridade GPU↔CPU (tolerância 4–8/255), gate
  `MATERIALIZE_GPU_TESTS=1`, serializada por mutex (um device wgpu de cada
  vez; a GPU é partilhada com os workers vramd).
