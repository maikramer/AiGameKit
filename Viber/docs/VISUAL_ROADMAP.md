# Fotorealismo — estudo do pipeline de render (2026-09-08)

Estado do pipeline inteiro (terreno, água, céu, luz, postfx, assets) medido no
worktree atual (passes r1–r3 + IBL vivo, uncommitted), com as lacunas que
bloqueiam fotorealismo e um roadmap priorizado. Números de perf:
[`docs/PERFORMANCE.md`](PERFORMANCE.md) (RTX 4050 6 GiB, simple-rpg 57–94 fps,
alvo 90 fps ≈ 11 ms/frame; volumétrico custa ~+8 ms fixos).

## Estado de implementação (execução 2026-09-08, uncommitted, VERIFICAÇÃO PENDENTE)

- ✅ **P0.1** — `dfg_lut` + `bluenoise_texture` no Cargo.toml (IBL/SSAO deixam
  os placeholders 1×1).
- ✅ **P0.2** — bake do `CliffMask` nas vertex colors (R = wall space, A =
  fator) via sistema em `plugin.rs` + marcador `CliffBaked` + teste.
- ✅ **P0.3** — `height.ktx2` (height-blend do par dominante) e `ao.ktx2`
  (oclusão por texel) carregados por layer; tabela bindless 0→69. Mapas de
  ROUGHNESS ficam na fila.
- ✅ **P0.4** — splat 32²→64² (1 m/texel; custo de bake de boot ×4 aceite).
- ✅ **P0.5** — água r2 com o port do `bevy_water` (ver secção própria):
  deslocamento de vértice + espuma de crista (jacobian) + buoyancy CPU +
  reflexo de céu pelo IBL (reflectance 1.0, tint residual 0.10) + IBL 128².
  SSR e o re-encode dos KTX2 sem mips ficam na fila.
- ✅ **P0.6** — docs corrigidos (AGENTS.md: layers PRODUÇÃO, gates reais).
- ✅ **P1.8** — sombras até 600 m (4 cascatas — o Bevy 0.19 tem
  `MAX_CASCADES_PER_LIGHT=4` hard-coded; shadow map 4096 já existia em
  `main.rs`).
- ✅ **P1.10 (parcial)** — `MotionBlur` + `ContrastAdaptiveSharpening` na
  câmara; debanding fica na fila.
- ⏳ **Verificação** — `cargo check/test`, `analyze` e QA visual ADIADOS
  (GPU ocupada a gerar assets). O harness novo `tests/water_shader.rs`
  compila o vertex da água em naga headless; `tests/chunk_shader.rs` cobre
  o WGSL do chunk.

## 1. O que já existe (não repetir)

Forward PBR do Bevy 0.19 com stack quase completa: **TAA + sombras PCSS
temporais + SSAO High + contact shadows + bloom + auto-exposure + DoF bokeh +
vinheta/CA + grading ASC CDL + volumétrico (FogVolume) + god-rays + domo de
céu procedural + IBL vivo** (`src/postfx.rs:240-483`, `src/ibl.rs`,
`src/sky.rs`, `src/recipes/spawn.rs:1106-1262`). O terreno usa material
bindless próprio com **PBR real** (`apply_pbr_lighting` no
`src/terrain/chunk.wgsl:369-709`): cascatas de sombra, luzes clusterizadas,
IBL, SSAO, fog in-shader. Água = `ExtendedMaterial<StandardMaterial>` com
ondas analíticas, fresnel, Beer–Lambert sobre o depth prepass, espuma, glint
sol/lua (`src/terrain/water.wgsl:197-363`). Props do pool já têm normal+AO em
todos os GLBs texturados (314/314) e ORM em 433/536.

## 2. Restrições duras (o que limita o roadmap)

1. **NV 595.84 SIGSEGV**: material custom com `#[texture]` crasha em
   `vkCreatePipelineLayout`. Qualquer material novo com texturas TEM de ser
   `#[bindless]` (guarda `test_chunk_material_stays_bindless`,
   `src/terrain/layer_material.rs:49-72, 635`).
2. **Deferred × materiais custom**: o trait `Material` faz default a Forward;
   `TerrainChunkMaterial` (bindless, layout próprio) ficaria fora do GBuffer.
   SSR do Bevy exige deferred → não é drop-in. Occlusion culling do Bevy é
   incompatível com deferred.
3. **Bevy 0.19 não re-uploads uniforms de material custom** → consts
   especializadas por mundo reescritas em disco a cada `run`
   (`src/main.rs:664-687`); `SKY_RADIANCE = 1.0` (material custom não recebe
   exposição da câmara).
4. **Volumétrico sem TAA = frame preto** (bissecção r1); atmosphere scattering
   + volumétrico juntos = não testado pelo próprio Bevy.
5. **Orçamento apertado**: 11 ms/frame com volumétrico já a comer ~8 ms;
   6 GiB VRAM com uma engine ≈ 1.3 GiB.

## 3. Lacunas principais (medidas, por impacto)

| # | Lacuna | Evidência |
|---|--------|-----------|
| 1 | **Máscara de cliff NUNCA chega às vertex colors** — o mesher transvoxel grava `WALL_NEUTRAL` constante (128/255, A=1.0) quando `uses_layer_material`. O gate triplanar regional, weathering brow→toe, AO de toe, streaks e musgo do `chunk.wgsl` estão INERTES em runtime. | `src/terrain/voxel/transvoxel_mesh.rs:44-45, 211-217`; contrato em `chunk.wgsl:528-533`, `src/terrain/cliffs.rs:273-296` |
| 2 | **LUTs do Bevy a usar placeholders**: `dfg_lut` (split-sum do IBL → resposta especular por roughness com textura 1×1) e `bluenoise_texture` (SSAO/contact shadows usam noise placeholder) não estão ativas no `Cargo.toml`. | `bevy_pbr-0.19.1/src/lib.rs:274-359`; Cargo.toml sem as features |
| 3 | **Água sem reflexo da cena nem refração real**: só IBL do céu + "cheap sky tint" analítico a ângulos rasantes; a cena atrás aparece por alpha blending; absorção mistura a cor do CORPO, não o fundo. | `src/terrain/water.wgsl:244-264, 305-328` |
| 4 | **Mapas do pool desperdiçados**: `height.ktx2`, `ao.ktx2`, `roughness.ktx2` existem por layer no pool; a engine só carrega `albedo`+`normal`. Sem height-blend, sem AO por texel, rough fixa por layer. | `examples/shared-assets/public/assets/textures/*/`; `src/terrain/splat.rs:71-86` |
| 5 | **Splat de baixíssima resolução**: 2 planos RGBA8 **32² por chunk** = 2 m/texel — margens de lagos, estradas e manchas têm bordas a 2 m esbatidas por bilinear. | `src/terrain/splat.rs:985` |
| 6 | **Achatamento agressivo por distância**: `FLAT_NEAR=42 m → FLAT_FAR=260 m` mistura o material para cor plana — aos ~100 m o detalhe já está >50% morto (anti-pop de LOD, anti-fotorealista). | `src/terrain/chunk.wgsl:173-178, 398-400` |
| 7 | **Sombras curtas**: 4 cascatas, máximo 300 m, shadow map 2048 — serras além de 300 m sem sombra; orçamento de 12 PointLights visíveis faz sombras pontuais "poparem". | `src/recipes/spawn.rs:1183-1192`; `src/ambient.rs:27, 489-524` |
| 8 | **43% das KTX2 embutidas nos GLBs sem mipmaps** (502/1180, concentradas em `characters/*_lod*`) → shimmer a média distância. O verificador do script de compressão já RECUSA `levelCount<=1` — é re-encode de pool, não engine. | `scripts/ktx2_compress_pool.py:125-132` |
| 9 | **Sem motion blur, sem debanding/dithering, sem LUT 3D**: grading é só ASC CDL por hora; céu + raymarch bandam (mitigado por TAA+jitter). | grep vazio; `src/postfx.rs:334, 358-364, 422-423` |
| 10 | **Tint dia/noite multiplica o ALBEDO** dos props (noite `[0.10,0.12,0.17]`) — não é iluminação, é escurecimento de textura; com IBL físico devia ser residual. | `src/grass.rs:458-470`; `src/prop_tint.rs:119-173` |
| 11 | **LoD de props = troca discreta de cena** (pop, sem crossfade; orçamento 24 swaps/frame); sem instancing GPU; vegetação sem ladder. | `src/render_lod.rs:72-137, 174-231` |
| 12 | **Volumétrico é 1 cubo (900×600×900 m) a seguir o herói**, `ambient_intensity: 0.0` — névoa noturna não apanha luz de fogueiras; sem volumes locais em cavernas. | `src/postfx.rs:353-367, 491-517` |

Derivas de docs a corrigir no mesmo passe: AGENTS.md ainda diz `layers`
"BLOQUEADO/`VIBER_CHUNK_LAYERS=1`" (runtime atual: ATIVO por default, escape
`=0`); comentário opt-in stale em `postfx.rs:349-351` (realidade: opt-out).

## 4. Roadmap

### P0 — quick wins (pouco risco, muito retorno por esforço)

1. **Ativar `dfg_lut` + `bluenoise_texture` (+ `area_light_luts` opcional)** no
   Cargo.toml: resposta especular do IBL correta por roughness, noise real em
   SSAO/contact shadows, LTC para luzes de área futuras. Sem risco de driver
   (LUTs embutidas do Bevy, não materiais custom).
2. **Assar `CliffMask` nas vertex colors dos chunks** (R = wall space, A =
   fator regional) num pós-processo do bootstrap — ressuscita triplanar
   regional, weathering, AO de toe, streaks/musgo. Sem shader novo; o
   contrato já está definido em `cliffs.rs:273-296`.
3. **Carregar `height/ao/roughness.ktx2` do pool por layer** e usar no
   `chunk.wgsl`: height-blend (picos de rocha/neve), AO por texel, rough por
   texel. Slots bindless disponíveis (tabela `range(0..37)`).
4. **Splat 32² → 64²** (1 m/texel): margens/estradas nítidas; memória
   irrelevante (4× de algo minúsculo).
5. **Água: reflexo do céu pelo IBL real** em vez do tint analítico a
   rasantes (amostrar o env map existente) + re-encode do pool sem mips
   (item 8 acima) — dois itens independentes.
6. **Corrigir docs/AGENTS.md** (layers, volumétricos opt-out) — barato e
   evita agentes futuros a atacar o sistema errado.

### P1 — médio (1–3 dias cada, com gate de env e A/B)

7. **Reflexo de cena na água**: SSR forward próprio (raymarch sobre
   depth+normal, aplicado SÓ à água, `VIBER_WATER_SSR=1` para iterar) ou
   reflexo planar para os maiores lagos. Não usar o SSR do Bevy (deferred ×
   materiais custom). Refração real do fundo vem junto (sample da cor da
   cena no depth prepass, não do corpo).
8. **Sombras**: 5ª cascata / mapa 4096 / máximo 300→600 m (cascata distante
   de baixa densidade) — as montanhas ganham sombra; afinar bias.
9. **Substituir `FLAT_NEAR/FLAT_FAR` por fade dithered/LOD-morph** (detalhe
   a 100 m+ em vez de cor plana) e reduzir a agressividade do fog de
   distância em conformidade.
10. **Motion blur** (`MotionBlur` + `MotionVectorPrepass` — componente de
    câmara, barato) + **debanding/dither** no fim da cadeia.
11. **Otimizar volumétrico**: half-res/step_count menor ou downsample (liberta
    os ~8 ms que financiam os itens 7–10); `ambient_intensity` com o IBL para
    névoa noturna responder a fogueiras.
12. **Grading por bioma**: attrs `pp-*` no parser do `BiomeRegion` → CDL por
    região (o comentário em `postfx.rs:422-423` já o antecipa). IBL: 6→12
    fases/dia e/ou 128².
13. **GPU occlusion culling** para props (forward-compatível; perf headroom
    reinvestível em densidade/qualidade) e LoD com crossfade/dither.

### P2 — longo prazo / risco alto (avaliar um de cada vez)

14. **Atmosphere scattering (Hillaire 2020, `bevy_pbr::atmosphere`)**
    substituindo o domo analítico: céu/fog/aerial unificados fisicamente. O
    próprio Bevy avisa que atmosphere + volumétrico juntos não está testado —
    exige protótipo A/B com `VIBER_NO_VOLUMETRICS=1`.
    **Progresso (2026-09-08):** primeiro passo implementado como modelo
    alternativo do domo — o raymarch Nishita (Rayleigh+Mie, 16×8) do
    `bevy_atmosphere` v0.13.0 portado para `src/sky.wgsl` + `src/sky_nishita.rs`
    (par CPU para o IBL), activável com `<Sky model="nishita">` ou
    `VIBER_SKY_MODEL=nishita` (default `analytic`, A/B por fazer). Ficou fora
    deste passo: unificação fog/aerial (o `AtmosphereState`/DistanceFog ainda
    derivam da paleta analítica) e o `bevy_pbr::atmosphere` nativo — avaliar
    depois do A/B.
15. **Deferred + SSR global** — cascata sobre o material bindless do terreno
    (shader deferred próprio), GBuffer extra na VRAM de 6 GiB, conflito com
    occlusion culling. Só se o item 7 provar que SSR paga a pena.
16. **Nuvens volumétricas 3D** no domo (o salto visual maior para céu
    fotorealista, e o mais caro).
17. **Instancing GPU de vegetação** (permite densidade ×10 do cap atual de
    800/tag) e meshlets (experimental, exige baking e features wgpu não
    exercitadas nesta stack NV).
18. **RectLights (LTC)** para fogueiras/lanternas; **irradiance
    volumes/lightmaps** para interiores; **POM** em props com height map;
    **transmissão screen-space** para vidro/cristais (substitui o hack de
    URL "crystal/glass" de `prop_tint.rs:60-98`).

## 5. Regras do plano

- Cada feature nova de render entra com **gate de env** (`VIBER_*`), A/B de
  screenshot via sessão partilhada e guarda de teste quando houver material
  novo (bindless obrigatório).
- Perf medido antes/depois: `viber debug prof --samples 10` + `stats()` —
  o orçamento é 11 ms e o volumétrico é a maior fatia atacável primeiro.
- P0.1/P0.2/P0.4 são seguros para começar já; P1.7 e P1.11 libertam o
  orçamento que os restantes P1 consomem.
