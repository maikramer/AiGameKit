# Performance — o que domina o frame no Viber

Medido no `simple-rpg` (mundo de 4 km, ~9700 cenas glTF, 61k entidades) numa
**RTX 4050 Laptop (6 GiB)**. Todos os números vêm de
`viber debug prof --samples 10` e `viber debug lua 'return viber.debug.stats()'`.

## O resultado

| Cenário | frame (média) | chunks | VRAM |
|---------|---------------|--------|------|
| Como estava: perfil dev, `opt-level = 0` | **114 ms** (8 fps) | 2047 | 2817 MiB |
| Release, sem culling/LOD | 29.3 ms (35 fps) | 2048 | 2817 MiB |
| Release + culling + ladder de LOD + `render-distance` | **10.7–17.4 ms** (57–94 fps) | 691 | 2817 MiB |
| **Tudo, com texturas KTX2** | 10.7–17.4 ms (57–94 fps) | 691 | **1340 MiB** |

**Cite o `frame_ms_avg`, não o `fps`.** O `fps` de uma amostra é instantâneo
e salta entre 22 e 148 no mesmo segundo. E mesmo o frame médio oscila entre
corridas nesta máquina: a RTX 4050 é portátil e o SM anda entre 2535 e
3105 MHz conforme a temperatura — as corridas a 10.7 ms e a 17.4 ms têm
composição idêntica (mesmos chunks, mesmos `culled`, mesmos meshes) e diferem
só no boost. Por isso a última linha é um intervalo, não um número.

As texturas KTX2 **não mexem no fps**, mexem na VRAM: um A/B com o chão em
`.webp` e em `.ktx2` deu 17.36 vs 17.35 ms.

A linha final corre com `cull-distance` 320 m em vez de 240 — 7% de fps por
33% mais alcance de vista.

**Resumo honesto: 114 ms → ~11–17 ms de frame (7–11x) e −52% de VRAM**, no
mesmo mundo e na mesma GPU.

Estado ao vivo (`viber.debug.stats()`): 10019 instâncias com `CullDistance`,
**7009 escondidas**; ladder de LOD com 2540 no tier 0, 559 no tier 1 e 2422
no tier 2.

## As quatro causas (por ordem de impacto)

### 1. O jogo corria sem optimizações

`viber run` delegava em `cargo run` **sem** `--release`, e o `--release` que
existia era emitido como `cargo --release run` — ordem inválida, portanto a
flag nunca funcionou. Bevy e Rapier a `opt-level = 0` são 4–10x mais lentos.

Corrigido em dois sítios:

* `viber run` corre **release por omissão** (`--debug` volta ao perfil dev);
* o `Cargo.toml` optimiza as dependências mesmo no perfil dev
  (`[profile.dev] opt-level = 1` + `[profile.dev.package."*"] opt-level = 3`),
  para o `--debug` continuar jogável.

### 2. Nada era cortado por distância

Cada `<StaticSpawner>` / `<Vegetation>` spawnava a cena glTF completa e ela
ficava no mundo de render para sempre: 61k entidades extraídas, testadas
contra o frustum e desenhadas em **quatro cascatas de sombra**, num mapa onde
o jogador vê ~300 m.

[`src/render_lod.rs`](../src/render_lod.rs) trata disso com o mesmo padrão do
`ScriptActivation` (o "LOD de IA"):

* `CullDistance` no **root** da instância — a visibilidade é herdada em Bevy,
  logo esconder o root apaga a subárvore inteira sem componente por malha;
* `NoShadowSubtree` marca a erva como não-projectora (`NotShadowCaster` é lido
  na entidade da malha, por isso este precisa mesmo de propagação);
* atributos XML `cull-distance` e `cast-shadows` em `<StaticSpawner>` e
  `<Vegetation>`; defaults 320 m (props), 80 m (erva, sem sombra), 160 m
  (criaturas de `<DynamicSpawner>` — o script já congela aos 45 m).

`cull-distance="0"` = nunca cortar. A faixa de horizonte
(`world/frontier/horizon.xml`) autora `cull-distance="1000"` porque a sua
silhueta é conteúdo de 450–840 m.

### 3. A ladder de LOD estava no XML e era ignorada

Os mundos migrados do VibeGame trazem `lod1-url`, `lod2-url`,
`lod-threshold-near` e `lod-threshold-mid` em 131 `<GLTFLoader>` — o Viber
listava-os como *dropped attrs* e desenhava a malha **hero** de cada pinheiro
a 200 m (`pine_dark_lod0` 1.4 MB vs `lod2` 0.32 MB).

`MeshLod` restaura a ladder. A troca é feita mutando `WorldAssetRoot`: o
`world_instance_spawner` do Bevy reage a `Changed<WorldAssetRoot>`,
despawna a subárvore antiga e spawna a nova — só **um** tier fica residente
por instância. Há histerese de 8% e um orçamento de 24 trocas por frame, para
uma viagem rápida não tentar re-tierar 6000 props num só frame.

Criaturas ficam de fora da ladder de propósito: trocar a subárvore
re-spawnaria o `AnimationPlayer` a meio de um clip.

### 4. `render-distance` do terreno cobria quase o mapa todo

Sem `render-distance` autorada, `effective_render_distance()` devolve o raio
que cabe `DEFAULT_RESIDENT_CHUNK_BUDGET` (2048) colunas — **1634 m** num mundo
de 4 km com colunas de 64 m. O far plane da câmara é ~1000 m, portanto metade
das colunas eram entidades que nunca chegavam ao ecrã mas pagavam
visibilidade, LOD e streaming de colliders todos os frames.

`world.xml` passou a autorar `render-distance="950"` → **689 colunas voxel
(900 caixas surface-nets)**.

## Terreno 100% volumétrico (custos próprios)

O terreno inteiro sai do campo voxel por surface nets, com ladder de LOD por
coluna (célula 1→2→4 m). Custos medidos (`chunk_build_bench`, release):

```
caixa 32³ @1 m (LOD0): ~5,8 ms   ← budget de 4 caixas/frame no rebuild
caixa 64³ @2 m (LOD1): ~7,2 ms
caixa 16³ @4 m (LOD2): ~1,6 ms
```

### O CSG das travessias sai de graça — depois de afinar o índice

O `qa-pontes.xml` põe ~230 mods no campo (duas pontes, gruta com sala e
chaminé, arco natural, viaduto e um campo `<RockFeatures>`). O custo por
caixa vem do `VoxelField::density`, que percorre os candidatos do bucket com
um teste de AABB por mod, ~39 k vezes por caixa de LOD0. Medido na coluna da
ponte de pedra:

```
célula do ModIndex = chunk_size (64 m):  37,7 ms/caixa   ← 3,5× o campo vazio
célula afinada (8 m neste mundo):         9,0 ms/caixa   ← paridade (7,1 sem mods)
```

Quarenta caixas de tabuleiro cabiam todas num bucket de 64 m, e cada amostra
pagava as quarenta. `ModIndex::build` passou a afinar a célula quando há mods
que cheguem (`REFINE_ABOVE_MODS`), com piso de 8 m e teto de 256 células por
aresta de mundo — a grelha continua pequena num mundo de 4 km. O bench
`bench_voxel_box_build_with_a_field_full_of_mods` falha acima de 30 ms/caixa
em release, que é o alarme para o campo voltar a não podar.

No `simple-rpg` (4 km, QA pós-migração): boot spawna 689 colunas / 900 caixas,
~89–103 fps de média — paridade com o ladder heightfield anterior. Os
colliders de terreno são todos **trimesh por COLUNA** (`ColumnColliderBake`,
`src/physics.rs`) assados dos mesmos triângulos que o transvoxel desenha,
streamados num raio de 3 chunk-edges com histerese;
`collision-resolution="0"` desliga-os.

## VRAM: as texturas eram PNG

169 GLBs do `simple-rpg` traziam texturas **PNG** (85 delas 2048x2048) — sem
`KHR_texture_basisu`. Um PNG é comprimido em disco e **descomprimido na VRAM**:
o GPU guarda RGBA8, 4 bytes por texel.

```
texturas em RGBA8 + mips : ~2121 MiB   ← 2.1 dos 2.8 GiB medidos
as mesmas em KTX2 → BC7  :  ~530 MiB
```

Medido depois de converter os GLBs e as texturas soltas: **2817 → 1340 MiB**,
sem uma única alteração às malhas ou aos materiais.

Estado final (verificado pelo próprio script): **559 GLBs de runtime com
textura, 100% KTX2, todos com supercompressão Zstd e cadeia de mipmaps
completa** (12 níveis a 2048², 11 a 1024², 10 a 512²), mais 189 texturas
soltas convertidas no `simple-rpg` e no pool.

`scripts/ktx2_compress_pool.py` converte um pool para KTX2/UASTC,
verificando cada ficheiro antes de substituir o original:

```bash
# GLBs (texturas embutidas) — substitui in-place
python3 Viber/scripts/ktx2_compress_pool.py --assets Viber/examples/simple-rpg/assets
# + texturas soltas (.png/.jpg/.webp) → ficheiro .ktx2 irmão
python3 Viber/scripts/ktx2_compress_pool.py --assets ... --loose
python3 Viber/scripts/ktx2_compress_pool.py --assets ... --dry-run
```

Duas exclusões deliberadas:

* **`_intermediate/`** fica de fora (`--include-intermediate` força). São
  entradas do pipeline (`rigging3d`, `animator3d`, `text3d lod`) lidas por
  bpy, que não lê KTX2 sem passar por
  `aigamekit_shared.gltf_decode.bpy_readable_glb`. Nunca chegam ao GPU:
  encodá-las não poupa um byte de VRAM e mete um round-trip lossy de UASTC
  em tudo o que for gerado a partir delas.
* **heightmaps e `images/`** — o primeiro é *dados* (um codec lossy destrói o
  terreno), o segundo é arte de referência que o motor nunca carrega.

As texturas soltas saem como `.ktx2` ao lado do original e as referências do
mundo (`texture=`, `src=`) passam a apontar para elas; normal/roughness/AO
são encodados como **linear** e o resto como **sRGB**. O `patch_image` em
`src/textures.rs` aplica o sampler REPEAT + anisotropia 8 *antes* de sair nos
formatos comprimidos, por isso o chão tiled continua correcto — mas o `.ktx2`
tem de trazer os seus próprios mipmaps (`--generate-mipmap`), porque a
geração de mips na engine só corre para RGBA8. O verificador do script recusa
qualquer `.ktx2` acima de 1x1 que venha com `levelCount = 1`.

Aviso ganho a doer: os campos do header KTX2 são `uint32` corridos a seguir
aos 12 bytes do identificador — `levelCount` está no **byte 40** e
`supercompressionScheme` no **44**. Lê-los nos offsets errados não dá erro
nenhum: dá "sem supercompressão" para todos os ficheiros, o que deixaria
passar precisamente o BasisLZ que o Bevy não carrega.

Tem de ser `uastc`, nunca `etc1s`: o Bevy 0.19 descomprime só ZLIB e Zstd, e
o ETC1S vem em BasisLZ. UASTC sem supercompressão (`scheme = 0`) serve — a
feature `basis-universal` transcodifica o *formato* para BC7 depois, que é de
onde vem a poupança.

O pool partilhado (`examples/shared-assets/public`) merece o mesmo tratamento;
o script aceita qualquer raiz de assets.

## Como medir

```bash
viber session up                       # porta livre automática
viber debug prof --samples 10          # média/pior/melhor — NÃO uma amostra só
viber debug lua 'return viber.debug.stats()'   # meshes/colliders/luzes com sombra
viber debug lua 'return viber.debug.physics()' # tempos do step do Rapier
```

O campo `fps` de **uma** amostra é instantâneo e oscila brutalmente enquanto o
terreno faz streaming (12 e 60 fps no mesmo mundo, com segundos de diferença).
`--samples` existe por causa disso.

Para isolar o efeito do culling + ladder sem trocar de binário:

```bash
VIBER_RENDER_LOD=0 viber run world.xml --no-cargo --bridge 15711
```


## Perfil 2026-09-09 (RTX 4050, simple-rpg, vila, spot fixo)

Metodologia: `viber session` + `viber debug prof --samples` + A/B por gates
de env com reinício de engine entre leituras.

| configuração | frame | fps | fatia isolada |
|---|---|---|---|
| tudo ON (baseline) | 23,0 ms | 43,8 | — |
| `VIBER_NO_VOLUMETRICS=1` | 19,4 ms | 51,6 | **volumétrico 3,6 ms** |
| `VIBER_NO_POSTFX=1` (chão) | 16,3 ms | 60,2 | **postfx restante 3,1 ms** (TAA+SSAO+MB+CAS+DoF+Bloom+prepasses) |
| — | — | — | **chão de render (sombras+cena) 16,3 ms** ← dominante |

- **GPU-bound**: CPU medido ≈ 4 ms/frame (physics.step 2,8 + travel 0,98 +
  resto ≤0,1) contra 23 ms de frame — os ganhos têm de vir do render.
- Fixos na mesma data: `travel_menu_system` passou o scan de entidades
  nomeadas a 4 Hz com cache (devolve ~1 ms/frame); day-tint do terreno
  48→16 degraus/dia (o burst de ~4 k materiais a cada 25 s era um soluço).
- Soluços periódicos: oscilações de ±850 MiB de VRAM e fps 37→20 em bursts
  alinhados com escritas de TODOS os materiais de chunk (day-tint, chuva) —
  fila: espalhar as escritas por frames.
- Na vila: 21 luzes com sombras (20 cubes point + direcional 4096²×4
  cascatas até 600 m) — próximas alvas de otimização sem perda visível:
  sombras de point light só num raio do player; medir 4096→2048 na
  direcional; volumétrico `step_count` 64→40 (jitter+TAA compensam).

## Varredura 2026-09-10 — cortes sem perda de qualidade

Varredura estática + A/B do que é mensurável. **Achado principal, e o corte
maior: o raymarch dos contact shadows corre POR LUZ E POR PIXEL.**

Em Bevy 0.19 `calculate_contact_shadow` está *dentro* dos três loops de luz de
`pbr_functions.wgsl` (point/spot/directional, chamado nas linhas 495/560/626),
não num passe único de ecrã. O gate por luz é
`POINT_LIGHT_FLAGS_CONTACT_SHADOWS_ENABLED_BIT`. Com até `LIGHT_BUDGET` = 12
tochas no cluster de um pixel, o `simple-rpg` pagava **12 raymarches fullscreen
de 24 passos por frame** — para um raio de 0,5 m que o shadow map da lanterna
já resolve. Numa PointLight o ganho visual é nulo; a técnica existe para o SOL
(herói→chão, poste→calçada) e fica lá. `VIBER_POINT_CONTACT_SHADOWS=1`
devolve-o.

O caminho do sol já era poupado: `light_budget_system` esconde as luzes fora
das 12 mais próximas e o extract do `bevy_pbr` **remove o `ExtractedPointLight`
de uma luz com `Visibility::Hidden`** (`render/light.rs:463`) — logo os cube
maps das escondidas não são desenhados. O orçamento já era eficaz; o que não
estava orçamentado era o raymarch por luz.

**Cortes de CPU** (por frame, verificado por `cargo test` + leitura do código):

| Sítio | Antes | Depois |
|---|---|---|
| `menus::campfire_banner_system` | `name.to_ascii_lowercase()` — **um `String` por entidade nomeada do mundo, por frame** (cada instância de spawner leva `Name`) | `contains_ignore_ascii_case` sem alocar + scan a 10 Hz |
| `player::dialogue_interaction` | varria todos os `DialogueNpc` com `distance` (sqrt) + `min_by` por frame e **descartava o resultado** (`let _ = near`) | só corre no frame em que [E] é premido |
| `ui::script::publish_ui_script_view` | `elements.clear()` + reinserção com `id.clone()` + `text.clone()` por elemento registado, mais `UiData.clone()`, em TODOS os frames | mapa actualizado no sítio (empréstimos, sem clones) + gates `Res::is_changed()`; um elemento novo é a única alocação |
| `hud::minimap` | `hypot` (sqrt) por comparação do `sort_by` + escrita incondicional de `UiTransform`/`Visibility` em cada dot/blip | distância ao quadrado + escritas guardadas |
| `hud::compass` | `*visibility` escrito em todas as letras/etiquetas por frame | escrita guardada |

As escritas incondicionais de UI importam mais do que o custo da escrita: o
Bevy marca `Changed` mesmo com o valor igual, e cada marca punha o Taffy a
refazer o layout do painel de HUD em todos os frames.

**NÃO mexido, e porquê:** `physics::stream_voxel_colliders` escreve
`status.ready` incondicionalmente, mas `player.rs:307` lê o campo **sem filtro
de `Changed`** — não há cascata nenhuma a cortar, e o campo é a autoridade de
"há chão carregado?" para o herói. `grass.rs` não é um problema: a relva é
**malha fundida por tile** (não uma entidade por lâmina).

**Gate novo para A/B:** `VIBER_VOLUMETRIC_STEPS` (default **64**, o valor de
sempre). O passe volumétrico é full-res e não tem knob de resolução interna, e
64→40 é a alva que a ronda anterior deixou em aberto — o gate existe para a
poder medir sem rebuild. O default NÃO mudou.

### Verificação de qualidade (contact shadows desligados)

Protocolo do user, com a correcção que faltava: **controlo do mesmo braço**.
Em `qa-visual.xml` (noite determinística, 4 lanternas com sombra), herói
teleportado para o mesmo sítio, 5 frames por arranque:

```
                                        max     média    pixels>2
CONTROLO  OFF r1 vs OFF r2 (2 arranques) 130    1,3178    29,81 %
TESTE     ON (antigo) vs OFF (novo)      130    1,2925    29,36 %
                                          razão 0,98x
```

O controlo e o teste dão a MESMA diferença: o que se vê entre os dois braços é
**variância de arranque** (história do TAA, convergência do AutoExposure), não
o corte. O brilho médio por canal é idêntico (82,5/86,0/94,5 vs 82,6/86,2/94,2).
**Sem controlo, este A/B teria sido lido como "13 % dos pixels mudaram" — a
diferença que se mede entre dois arranques do binário IDÊNTICO.** A lição vale
para toda a QA temporal deste repo.

### Pendente

O A/B numérico de frame no perfil release **não foi concluído**: outros agentes
mantinham engines vivas a 57–87 % de GPU durante a janela de medição, e nessa
condição a comparação mede contenção, não código. O harness fica pronto para
uma janela limpa:

```bash
VIBER_BIN=/tmp/viber-perf-ab/viber-after \
  tools/perf_ab.sh worlds/qa-visual.xml VIBER_POINT_CONTACT_SHADOWS 1 0 3
tools/perf_report.py            # medianas por braço + delta
```

`tools/perf_ab.sh` sobe/desce por `session up`/`down` (protocolo de QA), corre
os braços INTERLEAVED e grava 5 frames por braço; `VIBER_BIN` fixa uma cópia
privada do binário, porque com agentes a reconstruir `target/release` o A/B
trocava de código a meio.

### Ray tracing removido (mesma data)

A pedido do autor, o RT de hardware (`bevy_solari` + feature cargo `rt`) saiu
do repositório: `src/rt.rs`, a feature, as deps de sonda (`wgpu`/`pollster`), o
registo do `RtPlugin`, o ramo do solari no `postfx` e o `target-rt/` — **19 GB**
de build cache (o disco estava a 98 %). O SSR raster (`src/water_ssr.rs`)
sobrevive e é independente. Detalhe em `docs/VISUAL_ROADMAP.md`, item 7.

## 2.ª passagem (2026-09-10) — registo duplicado, partículas, materiais

Segunda volta sobre a mesma máquina, com o critério de sempre: só entra o que
não se vê. Duas coisas mudaram de método por causa das lições da 1.ª:

* **medir por SISTEMA, não por frame** — com engines de agentes paralelos a
  segurar a GPU (57–87 %) o frame time mede contenda; o `avg_ms` de um sistema
  embrulhado em `timed` mede-se à volta desse sistema e é muito mais robusto;
* **controlo no MESMO binário** (`VIBER_<X>=0`), porque comparar dois builds
  mistura as mudanças do próprio mundo com as dos vizinhos.

### Bug encontrado: o `timed` do tint do terreno era substituído em silêncio

`terrain_daynight_tint` estava registado **duas vezes** em `Update`:
`timed(Group::Terrain, …)` no `TerrainFeaturesPlugin`
(`src/terrain/runtime.rs`) e **cru** no `AmbientPlugin` (`src/ambient.rs`).
O `Timed` devolve `self.inner.system_type()`, portanto o Bevy vê o wrapper e a
função crua como **o mesmo sistema**: o registo posterior (o do
`AmbientPlugin`, adicionado depois no `main.rs`) sobrepõe o anterior e a versão
que sobrevivia era a **sem `timed`** — o sistema desaparecia do profiler, e
bastava a ordem dos plugins mudar para ele passar a correr **duas vezes por
frame**. Fix: um registo único, sob `timed`, e o `terrain_rain_wetness` mudou-se
para lá também (era o único sítio onde corria, e corria sem instrumentação).

Observabilidade, para quem for repetir isto: o snapshot do profiler corta a
lista de sistemas a **30** (`src/profiler/mod.rs:309`), portanto os sistemas
baratos (o chão molhado, o `drop_failed_terrain_textures`) **não aparecem** na
lista — não é sinal de que não correm.

### Partículas: gate de emissor vazio (`VIBER_PARTICLE_IDLE_GATE`)

O `particle_emitter_update` reescrevia `capacity × 4` vértices e re-uploadava o
mesh em todos os frames, **mesmo sem uma partícula viva**. O caso que o torna
caro é a chuva: o emissor está ancorado ao player, nunca é culled por distância
e, com a intensidade a 0, fica `Visibility::Hidden` com `emission_rate = 0` —
vivo, invisível e a trabalhar por nada. Agora escreve os zeros UMA vez e depois
cala-se.

Medido em `worlds/qa-tint-dry.xml` (rain 0 → é exatamente esse caso, 1 emissor),
mesmo binário, braços interleaved, 2 rondas, `prof --samples 8`:

| `particles::particle_emitter_update` | avg | max |
|---|---|---|
| sem gate (`VIBER_PARTICLE_IDLE_GATE=0`, antigo) | 0,00899 ms | 0,0398 ms |
| com gate | **0,00484 ms** | **0,0172 ms** |

**−46 % de média, −57 % de pico.** O frame **não mexe** (9,43 vs 9,42 ms): é
GPU-bound, como o perfil já dizia — o ganho é CPU e banda de upload, não frame.

### Terreno: as escritas de material passaram a ser orçamentadas

`terrain_daynight_tint` e `terrain_rain_wetness` escrevem o **mesmo valor** nos
params de **todos** os materiais de chunk (63×63 = 3969 no `simple-rpg`). Cada
`get_mut` num material bindless tem um custo conhecido do lado do render: o
upstream diz explicitamente que não há fast path para "só mudou o conteúdo do
buffer" — liberta o slot, **destrói e recria o bind group** com o array inteiro
de texturas e re-uploada o data buffer + a tabela de índices
(`bevy_pbr/src/material.rs`, junto ao `bind_group_allocator.free`).

As passagens ficaram orçamentadas a 64 materiais por frame
(`CHUNK_MATERIAL_WRITE_BUDGET`), com `VIBER_CHUNK_TINT_BUDGET=0` a restaurar o
"tudo num frame" para A/B. O valor muda devagar (16 passos por dia de jogo, ou
uma rampa de chuva de segundos) e é idêntico para todos, portanto espalhar a
passagem por ~1 s não muda um pixel — só deixa de existir o frame que paga a
conta toda. A convergência (o único falhanço silencioso possível: metade do
mundo presa no valor antigo) tem teste dedicado
(`test_sweep_slice_covers_every_material_once`).

**Honestidade sobre o benefício:** medido em `worlds/qa-tint.xml` (1024
materiais, braços `VIBER_CHUNK_TINT_BUDGET` 0 vs 64), o **custo do próprio
sistema é ~2 µs de média e ~4 µs de pico nos DOIS braços** — a mutação do asset
em si é barata; a conta está do lado do render (prepare), e o Bevy 0.19 não
expõe tempos por passe. **Este ganho NÃO está verificado.** Fica como mitigação
do sintoma que a 1.ª passagem mediu (±850 MiB de VRAM e fps 37→20 em bursts
alinhados com escritas de todos os materiais de chunk) e como knob pronto para
medir no dia em que houver visibilidade de render-side.

### Água: broad-phase no contacto

O `water_contact_system` perguntava a **cada** nadador por **todos** os corpos
de água, e o `WaterBody` de um rio resolve todas as queries com
`nearest_on_path` sobre o vetor inteiro de estações — O(estações) por consulta.
Com as ~170 criaturas do `simple-rpg` são dezenas de milhares de testes de
segmento por frame para 99 % de bichos em terra firme. Agora há uma caixa XZ
conservadora por corpo (`body_bounds`, uma vez por frame) que os corta antes da
query. O sistema não está instrumentado, portanto **sem número** — o que se
pode dizer é que é um reject exato (a caixa usa o pico do contorno orgânico e a
maior meia-largura de estação, nunca exclui um ponto que o corpo molhe).

### Ferramenta

* `worlds/qa-tint.xml` (2048 m → 1024 materiais, chuva 0,9) e
  `worlds/qa-tint-dry.xml` (rain 0) — bancos de teste baratos para o publish do
  terreno e para o gate de emissor vazio; nenhum traz props, criaturas ou
  scripts, para o custo medido ser o do sistema em teste.
* `tools/perf_ab.sh` ganhou a fase de **stress** (`VIBER_AB_STRESS=1`: salta o
  relógio por vários passos do dia, que é o que dispara as passagens) e `--port`;
  `tools/perf_report.py` filtra por fase e lê os timings por sistema (sufixo do
  nome canónico); `tools/perf_bin_ab.sh` compara dois BINÁRIOS.

### 3.ª passagem (2026-09-11) — leaks de assets, e o número que faltava

Dois fechos desta ronda:

**1. O corte dos contact shadows das point lights está MEDIDO — e era ainda
maior do que a leitura do código dizia.** Com a GPU finalmente livre (nenhuma
engine de agente paralelo durante a ronda), A/B no `simple-rpg` com o MESMO
binário (`VIBER_POINT_CONTACT_SHADOWS` 1 vs 0), braços interleaved, 2 rondas,
amostras a cada 500 ms:

| ronda | com contact shadows nas point lights | sem | Δ |
|---|---|---|---|
| 1 | 52,09 ms | 31,50 ms | 20,6 ms |
| 2 | 48,61 ms | 30,57 ms | 18,0 ms |

**~18-21 ms por frame — mais de um terço do frame inteiro.** O `raymarch` por
luz e por pixel (`pbr_functions.wgsl`) era o MAIOR custo de render do jogo.
Nota de contexto: o frame absoluto do `simple-rpg` hoje está nos ~31 ms (não
nos 23 ms do perfil de 2026-09-09) porque o mundo ganhou conteúdo de outras
frentes; o que aqui se mede é o DELTA entre os dois braços, no mesmo binário,
com os mesmos 57k entidades.

**2. Os leaks de assets fecharam — e são observáveis.** O Bevy não faz GC de
assets, e três caminhos deixavam materiais/malhas residentes **para sempre**:

* **hit-flash** (`feedback.rs`): cada golpe clonava até 24 materiais e nunca
  os libertava — o leak mais rápido, POR GOLPE. Agora o fim do flash REVERTE
  os handles para os originais e remove os clones (e isto é até mais fiel: o
  comportamento antigo deixava um clone com emissive a PRETO, que apagava o
  brilho de materiais emissivos originais);
* **cadáveres** (`combat.rs`): até 24 clones por abate, removidos no despawn;
* **bursts/ripples** (`particles.rs`/`water_fx.rs`): cada burst criava
  `Mesh`+`StandardMaterial` e cada anel de chuva um material — com a chuva a
  0,9 eram ~14/s. Agora as malhas de burst vivem num POOL por escadote de
  capacidade (≤ 7 malhas no total, zeradas ao devolver para não estrear com as
  partículas do burst anterior), há exactamente DOIS materiais de emissor
  (`Add`/`Blend`) e os anéis reciclam os seus num pool ≤ `MAX_RIPPLES`.

Verificação: testes headless que afirmam que a contagem do store VOLTA à linha
de base (`test_burst_assets_nao_acumulam`,
`test_hit_flash_remove_os_clones_no_fim`, `test_corpse_leva_os_clones`), e ao
vivo no `qa-raster` (chuva 0,9, ripples contínuos): **80 materiais / 287
malhas / 241 imagens, planos ao longo de 70 s** — antes só os anéis somariam
~1000 materiais nesse intervalo. Para isto ser visível na QA de sempre, o
`viber.debug.stats()` ganhou `assets.{meshes,materials,images}` — as contagens
do ASSET STORE, não de instâncias (o `meshes` antigo conta entidades e não vê
um material clonado que ninguém libertou).

Gotchas que esta ronda deixou escritos: no Bevy 0.19, `World::clear_entities()`
invalida fetches de recursos (descoberto à custa de um teste que se recusava a
passar); e `Commands::new(queue, &world)` aplica os comandos **no push**, não
no `apply` — o fecho da fila é imediato quando há ponteiro de mundo.

### Ainda em aberto (por ordem de €/ms)

1. **O publish do tint é estruturalmente caro, não só em bursts.** Os valores
   (`day_tint`, `sun_dir`, `walls_b.w`) são **globais do mundo** mas vivem nos
   params de cada material, portanto qualquer mudança custa N mutações de
   asset. O fix verdadeiro é publicá-los **uma vez** — um uniform/storage
   global lido pelo `chunk.wgsl` em vez de 3969 cópias. É uma mudança no layout
   do material bindless, na zona que já teve um SIGSEGV documentado do driver
   NV: fazer com `test_chunk_material_stays_bindless` à frente e uma medição
   render-side disponível.
2. `prop_daynight_tint` (`src/prop_tint.rs`) muta **todos** os
   `StandardMaterial` do mundo a 4 Hz no amanhecer/anoitecer — mesmo mecanismo,
   mesmo custo, e sem orçamento nenhum. Candidato óbvio à mesma disciplina.
3. `water_ambience_driver` faz a segunda passagem por todos os corpos por
   frame (uma posição só — barato, mas mudou de ordem de grandeza com o rio).
4. **Tooling:** os 30 sistemas do snapshot escondem precisamente os sistemas
   baratos que se querem confirmar; e o `frame_ms.max`/`p95` do snapshot vêm a
   zero, portanto o pico do frame não é observável por esta via. Um gate
   `VIBER_PROF_SYSTEMS` resolveria o primeiro.
