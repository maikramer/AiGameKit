# p1-stability — lista de problemas encontrados e corrigidos (ronda 1 + ronda 2)

Runs de referência: `VIBER_DEBUG_KEYS=1 cargo run -p viber --release -- run
examples/simple-rpg/world.xml --bridge` (RTX 4050 Laptop 6 GB, 1280×720, preset
`equilibrado`). Ronda 2: binário com `98abe560` + `de0cc415` + `e26f577e`; log
pós-fixes: `log-after.txt`; passo-a-passo com labels: `walkthrough-timeline.md`.

## RONDA 1

### 1. CRASH no arranque — `TerrainChunkMaterial` rejeitado pelo wgpu (bloqueante) ✅ CORRIGIDO

- **Sintoma**: a engine SAÍA antes do loading screen: `ERROR bevy_render::error_handler: Validation Error — In Device::create_bind_group, label = 'TerrainChunkMaterial': Binding count declared with at most 8 items, but 58 items were provided` → `Quitting the application due to Validation RenderError`.
- **Causa-raiz**: o material único de terreno (bindless, 69 pares `#[texture]`/`#[sampler]`) declarava `#[bindless(limit(8), …)]`. O `limit` é o nº de descritores do SLAB: o layout declara as binding arrays com esse count e o material leva ~58 descritores únicos → o wgpu recusa o `create_bind_group` e a app sai.
- **Fix (engine)**: `Viber/src/terrain/layer_material.rs` — `limit(8)` → `limit(128)` (≥ 69 descritores com folga; 69 × 128 = 8 832 elementos de sampler, 14× abaixo do cap do device). Comentário reescrito com os DOIS tetos.
- **Teste de regressão**: `test_bindless_slab_limit_holds_the_whole_material` — afirma limite explícito, `limit ≥ descritores` e `samplers × limit ≤ 131 072`.
- **Commit**: `464cf115` (árvore partilhada do orquestrador).

### 2. Covil dos contrabandistas com 0 guardas — a `<SpawnExclusion>` engolia o próprio spawner ✅ CORRIGIDO

- **Sintoma**: `spawn '/assets/meshes/characters/bandit_lod0.glb': 0 placed / 192 attempts (rejected 192 — occupancy 192)`. O covil da quest `smugglers_cache` ficava SEM os 6 bandits.
- **Causa-raiz**: a `<SpawnExclusion at="-90 240" radius="34">` que protege o acampamento cobria a PRÓPRIA região de spawn da guarnição — e exclusões são globais por contrato.
- **Fix (engine)**: `Viber/src/spawner.rs` — discos de exclusão num registo PRÓPRIO (`register_exclusion`); `is_free(…, honor_exclusions)` testa os dois registos por omissão. `Viber/src/recipes/mod.rs` — attr `honor-exclusion` (default `1`). `Viber/src/bake.rs`, `Viber/src/scene/tools.rs` — registo migrado.
- **Fix (conteúdo)**: `Viber/examples/simple-rpg/world/scenes/smugglers.xml` — guarnição com `honor-exclusion="0"`.
- **Teste**: `test_spawn_exclusion_blocks_and_honor_exclusion_opts_out`.
- **Commit**: `a235d246` + contrato no `Viber/AGENTS.md`.

### 3. WARN `Could not despawn entity` em rajosa no gameplay ✅ CORRIGIDO (2 frentes)

- **Causa-raiz**: entidades transitórias recebem `despawn()` em fila; quando o comando chega depois de a entidade já ter morrido, o handler default do Bevy warna. Segunda fonte: o pipeline de screenshots da bridge (duplo despawn por captura).
- **Fix (engine)**: (a) 20 sítios de despawn TRANSITÓRIO → `try_despawn()` (`menus.rs`, `feedback.rs`, `combat.rs`, `harvest.rs`, `skills.rs`, `physics_fx.rs`, `projectile.rs`, `particles.rs`, `terrain/water_fx.rs`) — commit `774ce5ad`; (b) screenshot store → `world.try_despawn` — commit `b8deb5c2`.

### 4. WARN spam `glTF de colisão atrasado — fallback AABB` (2521 linhas num walkthrough) ✅ CORRIGIDO

- **Fix (engine)**: `Viber/src/physics.rs` — throttle: o 1.º do rajoso warna com a contagem suprimida, janela de 30 s. Commit `b8babf59`.

### 5. ERROR `could not set cursor position` a cada clique sintético — INVESTIGADO; solução parcial documentada

- **Causa-raiz**: `Window::set_cursor_position` escreve a posição CURRÍCULA e o `bevy_winit` tenta sincronizá-la ao winit real; o Wayland recusa sem o ponteiro capturado.
- **Tentativa e REVERSA**: gatear o write matava o hover sintético — os cliques de QA deixaram de comprar na loja. Revertido em `049cad0f`. **Decisão**: funcionalidade (clique sintético) > 1 linha de ERROR por clique de QA (Wayland-only, QA-only). Na ronda 2: 2 ocorrências (2 cliques), sem impactos.

### 6. Resíduo (a) — erro shader WGSL no log (loop 1) ✅ JÁ RESOLVIDO

`src/sky.rs` migrou para WESL; nenhum run tem erro de compilação WGSL/WESL.

### 7. Resíduo (b) — BGM por bioma: troca por ZONA operante ✅ VALIDADO com instrumentação nova (r2)

- Os 6 `<MusicLayer>` do mundo cruzam por ZONA (`src/music.rs`, testes unitários `test_bgm_zone_*`).
- **r2**: o tab Áudio passou a expor o volume CORRENTE por layer (commit `e26f577e`) — os dumps `bgm-*.json` deixaram de ser bit-idênticos: vila → **village 0.18**, Ermo/floresta/pântano → **explore 0.18**, Picos → **mountain 0.2**, aggro → **battle 0.22** (hold 8 s), cada um com as restantes layers a 0.0. Crossfade demonstrado com timestamps no `walkthrough-timeline.md`.
- **Achado r2 (observado, NÃO corrigido)**: as MusicLayer morrem com o varrimento da cena principal — dentro da capela o tab Áudio mostra 0 layers BGM (o `dungeon` não toca em interiores). Os testes unitários do driver continuam verdes; o fix é mover as layers para autoload/persistentes — P6.

### 8. GLITCH golden hour — ✅ CORRIGIDO NA RONDA 2 (ver §10)

### 9. Observado e NÃO corrigido na r1 (com justificação) — estado na r2

- **Queda através do mundo em teleportes rápidos de QA** — segue QA-only (rede de segurança salva sempre). O RESPAWN de gameplay (a queixa do crítico) foi corrigido na r2 (§11). Timing de QA medido na r2: mundo completo ~3 s após o tp (`13c/13d-qa-tp-stream-*.png`).
- **`ERROR landmass_rerecast` (vendorizado)** — 3× na r1, 4× na r2 (cresce com tiles de montanha visitados). Mesma classe: validador do fork rejeita polígonos do DETAIL MESH do próprio rerecast. Fix = patch ao vendor — fora de âmbito.
- **`WARN mesh sem atributos de normal` (1×/boot)** — GLBs do pool sem NORMAL; reparo é do pipeline de assets (`text3d`).
- **`WARN viber.gesture(…) sem CharacterAnimator`** — 1 linha por fala de NPC sem clip; informação de autoria.
- **`WARN sctk_adwaita::buttons` (1×/boot)** — decoração Wayland.
- **`WARN terrain: rock field seeded 9 of 10`** — informativo do `RockFeatures`.

## RONDA 2

### 10. Golden hour renderizava o frame INTEIRO vermelho/magenta (o GAP do crítico) ✅ CORRIGIDO — commit `98abe560`

- **Sintoma**: com o relógio em 05:46 ou 19:16 (`debug.set_clock`), TODO o frame (terreno, props, personagem, céu, UI por alpha-blend) satura em vermelho/magenta; 12:01 e a noite renderizavam limpos (`18-golden-hour-bug.png` da r1 vs `17-dia-limpo.png`).
- **Causa-raiz REAL (confirmada por matemática + A/B ao vivo)**: `grade_temperature_full` escrevia `golden×0.35 + low_sun×0.10` em `ColorGrading::global.temperature` tratando o campo como "stops de film stock". No Bevy, `temperature` é um **desvio da cromaticidade *x* CIE 1931** aplicado ao branco D65 (`white_point_xy.x = 0.3127 − temperature`; matriz `balance` pré-calculada no CPU em `bevy_render::view::From<ColorGrading>`). Com 0.445 (pico golden ≈ 0.99 + sol baixo = 1) a matriz mandava branco → **[8.8, −0.09, 0.42]**: o canal VERDE ficava negativo, o shader clampe a 0 (`max(balance·color, vec3(0))`) e cada pixel lia vermelho/magenta. O meio-dia ficava limpo porque golden=0 → temperature=0. Os isolamentos da r1 (não é bloom/splittone/volumetrics/autoexposure) eram consistentes: nenhum deles toca o CDL.
- **Fix (engine)**: `Viber/src/postfx.rs` — braços quentes reescalados para cromaticidade real: `GOLDEN_GRADE_TEMPERATURE = 0.035` (novo) + `GOLDEN_GRADE_WARMTH = 0.010` (era 0.10). Pico quente total 0.045 → branco [1.40, 0.92, 0.89], relva [0.36, 0.46, 0.13]: dourado legível EM GAMUT, com o aquecimento forte a viver na KEY da luz (`GOLDEN_KEY_COLOR`, intacta) e no céu dourado. A noite fica em −0.25 INTACTA — o azul aprovado pelas LOOPs A/B/C (o lado frio empurra R de neutros para baixo por decisão de look; só o lado quente destruía gamut).
- **Teste de regressão**: `test_golden_grade_warm_side_stays_in_gamut` — replica a matriz `balance` REAL do Bevy (constantes do upstream) e varre −15°..62° de elevação solar: nenhuma hora quente do relógio pode emitir um `temperature` que destrua canal em branco/relva; o lado frio guarda o VERDE (o canal que o bug destruía); guarda de sanidade prova que +0.445 destruiria (o teste apanha a classe do bug).
- **Verificação in-game (A/B `debug.set_clock`)**: 12:01 limpo ↔ 19:16 entardecer dourado ↔ 05:46 amanhecer frio legível — `screenshots/17-dia-limpo.png`, `18-golden-hour-1916.png`, `18b-golden-hour-0546.png`.

### 11. Respawn acordava num vazio de névoa (secundário do crítico — fluxo de gameplay) ✅ CORRIGIDO — commit `de0cc415`

- **Sintoma**: r1 `14-boss-sul.png`/`15-respawn.png` mostravam o mundo 3D vazio (só névoa + herói) após morte+respawn e QA-teleports.
- **Causa-raiz**: o respawn teleporta o herói ao ponto mais próximo, que pode estar fora da janela de colunas construídas (morte em banda distante, saída de interior); a ladder de terreno reconstruía ao orçamento normal (4 caixas/frame) — os primeiros segundos eram névoa. Os teleportes de QA partilham o mecanismo mas são QA-only (documentado abaixo).
- **Fix (engine)**: `Viber/src/cinematic.rs` — recurso `RespawnStreaming` (timer) ao lado do `StreamingBoost`; `feedback::respawn_system` arma 2.5 s no salto; `tick_cinematic` desce o timer e FUNDE-o no `StreamingBoost` que o terreno (×10), o render LOD e a relva já multiplicam — zero mudanças nos consumidores. Par agrupado em `StreamingBoostParams` (`SystemParam`; o sistema já vivia no teto de 16 params do Bevy).
- **Testes**: respawn arma a janela (`test_respawn_clears_momentum_and_settles` estendido) + matemática do timer pura (`test_respawn_streaming_boost_window`).
- **Verificação in-game**: morte no deserto → respawn no portão leste: **+0.5 s o mundo já está lá** (`14c-respawn-mundo-0.5s.png`, `14d-respawn-mundo-1.5s.png`); boss-sul repicado com mundo completo (`14-boss-sul.png`). Timing QA-teleport (sem boost, por desenho): névoa a 1 s → completo a 3 s (`13c/13d-qa-tp-stream-*.png`).

### 12. Higiene de evidência (pedidos do crítico) ✅ FEITO nesta ronda

- **Timeline com labels por linha**: `walkthrough-timeline.md` — cada linha S1–S16 com valores nomeados (posição, vault antes/depois, xp, cena, layer BGM ativa).
- **Captura de vault correta**: S4 com `ouro=32` ANTES do clique e `ouro=7 pocoes=2` DEPOIS (25 gastos) — a compra lê-se inteira.
- **BGM com instrumentação**: commit `e26f577e` — o tab Áudio expõe o volume CORRENTE por layer; dumps `bgm-{vila,ermo,floresta,pantano,picos,battle}.json` agora DISTINTOS por zona, timestamps no timeline.
- **Boss-sul/respawn repicados com mundo visível**: `14-boss-sul.png`, `14c/14d`, `15-respawn.png`.
- **Ruído preservado (não regrediu)**: os ERRORs conhecidos continuam documentados (§9) — 2× cursor Wayland + 4× landmass_rerecast no `log-after.txt`, zero panics.
