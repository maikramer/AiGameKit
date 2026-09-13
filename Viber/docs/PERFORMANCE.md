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

### 4.ª passagem (2026-09-11) — o orçamento de luzes deixou de apagar a vila

O `LIGHT_BUDGET` antigo escondia (`Visibility::Hidden`) todas as PointLights
além das 12 mais próximas — **apagava-as mesmo**: ao andar pela vila à noite,
as lanternas além da 12.ª mais próxima estavam FORA, e piscavam ON/OFF ao
mudar o ranking (o refresh é de 1 s). Não era um limite da engine: o cluster
do Bevy aguenta **204** objetos (`MAX_UNIFORM_BUFFER_CLUSTERABLE_OBJECTS`) e o
`range` por omissão (20 m) já limita o custo por pixel às luzes que tocam o
pixel.

A política nova separa as duas coisas:

* **Luz** — TODAS iluminam (o `simple-rpg` tem 130; cabem nas 204).
* **Sombra** — o cube shadow map (6 faces de cena por luz, a parte cara) é
  orçamentado às `SHADOW_LIGHT_BUDGET` = 12 mais próximas **com sombra
  autorada** (`shadows="true"` no XML, marcadas por `AuthoredShadowLight` —
  sem o marcador, "desligada pelo orçamento" seria indistinguível de "autorada
  sem sombra"), com banda de histerese (`SHADOW_LIGHT_BAND` = 4) para a
  fronteira não trocar a cada refresh. Sombras a 20+ m não se leem; luz a
  20 m lê-se.

`VIBER_LIGHT_BUDGET=12` devolve a política antiga para A/B. Com os ~18-21 ms
devolvidos pelos contact shadows (ver a 3.ª passagem), a vila à noite fica
toda acesa em vez de só os 12 candeeiros da vez.

**Medido** (A/B no mesmo binário, braços interleaved, noite fixa por
`set_clock(1320)`, câmara igual):

| sítio | política antiga (≤12 luzes) | nova (todas) | Δ |
|---|---|---|---|
| spawn (poucas luzes próximas) | 29,23 / 28,81 ms | 29,09 / 28,89 ms | ~0 |
| (-14, 14) — **21 luzes num raio de 25 m**, o ponto mais denso do mundo | 27,85 ms | 28,28 ms | **+0,43 ms** |

E o diff de imagem no ponto denso mostra as lanternas que a política antiga
mantinha APAGADAS a arder na nova (faixa superior do enquadramento: as tochas
da direita passam de apagadas a acesas). No spawn o diff é nulo — ali a
política antiga não escondia nada, e a nova também não custa nada.

O ponto denso saiu de uma análise offline do XML (posições-mundo das 159
`PointLight` acumuladas pela hierarquia de `<Group>`): (-14, 14) com 21 luzes
num quadrado de 50×50 m. É o sítio de QA para este orçamento.

Na mesma passagem, o `prop_daynight_tint` ganhou o orçamento por frame que o
terreno já tinha (`PROP_TINT_BUDGET` = 64/frame, a mesma `sweep_slice`): ele
muta TODOS os `StandardMaterial` e, no amanhecer/anoitecer, o alvo muda de
forma contínua durante minutos — sem orçamento eram 4 passadas completas por
segundo nessa janela, cada uma com o pico dos bind groups recriados num único
frame. O alvo continua a recalcular ao throttle de 0,25 s; a APLICAÇÃO é que
se espalha por frames.

### 5.ª passagem (2026-09-12) — o OOM que matou uma sessão de 10 h

Uma sessão do `simple-rpg` com 10 horas de jogo morreu a 2026-09-12 03:06
com `Quitting the application due to OutOfMemory RenderError` — a alocação
que falhou é a **`point_light_shadow_map_texture`**, e as `Validation Error`
que se seguem ("Texture is invalid") são a consequência de a textura não
existir.

A aritmética do array: `size² × 4 B (depth) × 6 faces × nº de luzes com
sombra`. A 1536 (o valor do passe visual r1) são **~56 MB POR LANTERNA** —
com o orçamento de sombras cheio (12 luzes), **~680 MB** só para as sombras
das point lights, num GPU de 6 GB partilhado com engines de agentes. Pior: o
array é REALOCADO inteiro quando a contagem muda (o `texture_cache` do Bevy
troca o descritor) — pico transitório de ambos os arrays, ~1,4 GB a 1536. É
uma recriação rara (só quando muda o Nº de luzes com sombra em alcance), mas
acontece precisamente quando o jogador entra/sai da vila — e foi isso que
encontrou a VRAM cheia às 03:06.

**Fix:** `PointLightShadowMap` passa a **1024** (~25 MB/lanterna, ~300 MB no
pior caso, pico de realocação ~650 MB). O r1 tinha subido para 1536 pelos
GRANULADOS das sombras das árvores — na **direcional**; nas point lights o
mapa cobre o `range` (20 m), portanto ~2,6 cm/texel a 1024 contra ~1,7 a
1536, com o PCSS a abrir a penumbra a partir do `radius` da lanterna — o
caster está a metros da luz. `VIBER_POINT_SHADOW_SIZE=1536` devolve o r1 para
A/B visual (o par tem de ser julgado com o protocolo do controle antes de se
afirmar diferença nenhuma).

Nota do post-mortem: o log da sessão mostra também uma tempestade de
hot-reload (os mesmos ~38 scripts a recarregar 3×/s durante ~1 min às 16:56
e de novo às 03:06) — consistente com um agente a gravar scripts em lote,
não com um ciclo de feedback da engine; e milhares de `CommandQueue has
un-applied commands` no encerramento, que é o ruído da morte a meio de fila,
não a causa.

### 6.ª passagem (2026-09-12) — estudo a fundo do caminho de render

Mapa completo do frame por passes (estudo de código; tempos por vir do
`VIBER_PROF_GPU=1`, ver abaixo). A cadeia: prepass depth+normal+motion →
SSAO (full-res, 18 spp) → shadow passes (4 cascatas 4096² da direcional +
cubes 1024² das ≤12 point lights mais próximas) → passe principal forward
com clustering (159 luzes; ~4-8 tocam o pixel típico da vila) → volumétrico
(full-res, 64 passos) → TAA → MotionBlur → Bloom → DoF → AutoExposure →
vinheta/CA → CAS → tonemap. A água não custa nada sem água no frustum; o SSR
é OPT-IN e está fora do frame (o AGENTS.md dizia "DEFAULT ON" — corrigido).

**Veredictos visuais (protocolo do controlo — mesmo braço corrido 2×, 5
frames, enquadramento e relógio fixos):**

| par | TESTE | CONTROLO | veredicto |
|---|---|---|---|
| sombras ponto 1024 vs 1536 (noite, qa-visual) | média 0,128/255, 0,01 % dos píxeis >2, max 20 | 0,020/255, 0,02 %, max **26** | **validado invisível** — o controlo tem máximo MAIOR; ficam os −380 MB |
| terreno com/sem salto de fetches (qa-tint, horizonte) | 0,688/255, 0,22 % >2, max 110 | 0,322/255, 0,23 %, max 83 | **validado nulo** — os poucos píxeis >50 (0,003 %) estão no MESMO sítio que o controlo (fronteira do horizonte, jitter do TAA); sem anel no limiar |
| volumétrico 64 vs 32 (noite) | 0,208/255 | 0,119/255 | **sem decisão** — à noite o volume está atenuado e o efeito mal foi exercitado; default MANTIDO a 64, gate para um teste de DIA com god-rays |

**Dois cortes desta passagem:**

1. **`PointLightShadowMap` 1536→1024** (`VIBER_POINT_SHADOW_SIZE` devolve o
   1536). Ver a 5.ª passagem: ~56 MB/lanterna → ~25 MB, o array inteiro
   desce de ~680 MB para ~300 MB e o pico de realocação (quando muda o nº de
   luzes com sombra em alcance) cai para metade. O r1 tinha subido para 1536
   pelos granulados das sombras das ÁRVORES — na direcional; nas point lights
   o mapa cobre o `range` (20 m, ~2,6 cm/texel a 1024) e o PCSS abre a
   penumbra. Verificação visual pendente da bateria.
2. **Salto de fetches do terreno na banda plana** (`chunk.wgsl`). O fragment
   faz 4 fetches por layer com peso (albedo, height, AO, normal); a partir de
   `FLAT_SKIP` = 0,995 de `flat_mix` (~335 m), o albedo já é ~100 % cor plana
   e a normal já pesa < 0,5 % — os fetches de ALBEDO e NORMAL saltam nessas
   layers (o HEIGHT fica: alimenta os pesos; o AO fica: escurece a distância
   pelos mips). Nulo por construção no limiar; poupa 2 dos 4 fetches por
   layer em toda a banda de fundo. O harness naga (`tests/chunk_shader.rs`)
   valida em todas as combinações de defines e o guard
   `test_chunk_material_stays_bindless` continua verde.

**Observabilidade nova (de frente paralela, adoptada aqui):** `VIBER_PROF_GPU=1`
liga o `RenderDiagnosticsPlugin` com `TIMESTAMP_QUERY` — o snapshot do profiler
passa a trazer `render_spans` com `cpu_ms`/`gpu_ms` POR PASSE
(`main_opaque_pass_3d`, `shadows`, `bloom`, …). É a visibilidade render-side
que faltava a todas as passagens anteriores; e `VIBER_PROF_SYSTEMS` sobe o
corte de 30 sistemas do snapshot. A bateria desta passagem corre com o gate
ligado nos dois braços (o overhead é comum, os deltas ficam válidos).

**Timing — tentativa INVÁLIDA, instrumento validado.** A janela de 15:55
durou 1 min: um peer ligou a meio da bateria e o frame derivou 12→18→18→36 ms
dentro de 3 min (as medianas são lixo). Mas o `VIBER_PROF_GPU` FUNCIONOU —
primeira composição por passe alguma vez medida (qa-tint, braço menos
contaminado, frame 12 ms):

| passe | gpu_ms | | passe | gpu_ms |
|---|---|---|---|---|
| main_opaque_pass_3d | 1,55 | | taa | 0,10 |
| ssao | 0,40 | | clustering | 0,07 |
| bloom | 0,16 | | restantes 12 | < 0,07 cada |

Os passes GPU somam ~2,6 ms num frame de 12 — nesse instante o frame não era
pass-bound (contenção de apresentação). E **o passe de sombras NÃO aparece na
lista**: o `RenderDiagnosticsPlugin` não instrumenta as shadow views — as
sombras continuam invisíveis a esta medição (a fatia "chão" de 16,3 ms do
perfil antigo continha-nas). A bateria ficou endurecida (verifica a janela ao
fim e avisa) e os números de ms dos dois cortes ficam para a próxima janela
séria — a chave dos spans no JSON é `gpu` (não `render_spans`).

**O piso de ruído do frame NESTA máquina (medido a fechar a ronda).** A
mesma engine, o mesmo mundo de 922 entidades, enquadramento e relógio fixos,
leituras consecutivas de `prof --samples 5` ao longo de ~50 s:

```
32,9   29,6   74,5   13,3   16,7   16,7  ms     (clocks a 2520 MHz, 60 °C)
```

**5,6× de variação sem NENHUM processo nosso visível** — são os bursts do
próprio ambiente de trabalho (compositor, browser, …) a roubar a GPU em
rajadas. Consequências, com evidência: o A/B `VIBER_POINT_SHADOW_SIZE`
1024-vs-1536 deu "1024 mais lento" DUAS VEZES (16,7 vs 10-11 ms) — e o
controlo (1024 vs 1024) deu 16,7/16,7: era o estado da máquina, não o
tamanho. **Comparações de frame de poucos ms são irresolveis neste estado**;
só efeitos grandes (os −18 ms dos contact shadows) ou janelas realmente
ociosas (utilizador ausente) medem. Os dois cortes desta passagem ficam
justificados pelo que neles é determinístico — a aritmética da VRAM e a
validação visual com controlo — e os ms ficam para uma máquina calma.

**Gates novos desta passagem** (todos com o default INALTERADO, para A/B):
`VIBER_POINT_SHADOW_SIZE` (1024), `VIBER_DIR_SHADOW_SIZE` (4096),
`VIBER_SSAO=low|medium|high|ultra` (High com TAA), `VIBER_VOLUMETRIC_STEPS`
(64), e os de frente paralela `VIBER_SHADOW_CASCADES` (4) /
`VIBER_SHADOW_DISTANCE` (600) — as alavancas nº 2 e 4 do ranking já podem ser
medidas sem rebuild no dia em que a máquina deixar.

**Ranking das alavancas que restam** (do estudo; €/ms estimado):

1. Volumétrico 64→32 passos — gate existe, decisão pendente da bateria.
2. Cascatas da direcional: `maximum_distance` 600→300 e/ou 4096→2048 — toca
   a decisão P1.8 (serras ao fundo com sombra do sol); exige A/B visual
   sério antes de mexer.
3. ~~Fetches do terreno >340 m~~ — cortado nesta passagem (ver acima).
4. SSAO High→Medium (18→8 spp) — o TAA limpa o ruído extra; regime já
   exercitado pelo braço `VIBER_NO_TAA=1`.
5. Contact shadows do SOL (24 passos, ~1,5 ms estimado) — deliberadamente
   MANTIDO: é o único que sobra e é o que faz a leitura "herói assente no
   chão".

### 7.ª passagem (2026-09-12) — o frame estava cego, e metade dele eram sombras que não se viam

(Complementa a 6.ª: ela mapeia os passes por leitura de código, esta mede-os.)

**O problema de medição primeiro.** O profiler só media os sistemas que nós
embrulhamos com `timed`: no `simple-rpg` isso dava **4 ms de um frame de
44 ms**, e os outros 40 não tinham nome nenhum. Três instrumentos novos
fecharam o buraco:

| Instrumento | O que mede | Gate |
|---|---|---|
| `gpu[]` no snapshot | cada span do render graph em CPU e GPU (`main_opaque_pass_3d`, `early prepass`, `ssao`, `bloom`, `taa`…) via `RenderDiagnosticsPlugin` + `TIMESTAMP_QUERY` | `VIBER_PROF_GPU=1` |
| `sched.*` | fatia de cada schedule do `Main` + **`sched.render_wait`** (o buraco entre o fim do `Last` e o `First` seguinte: extract + render app + present) | sempre |
| `render.*` | fases do schedule `Render` no sub-app (`prepare_views`, `queue`, `phase_sort`, `bind_groups`, `render`…) | sempre |
| `bevy.*` | fases do `PostUpdate` do próprio Bevy (transform propagate, visibilidade, visibilidade por luz, clusters) | sempre |

Mais `shadow_lights` nos contadores (PointLights com cube shadow map activo) e
`VIBER_PROF_SYSTEMS=<n>|all` para tirar o corte de 30 linhas da lista de
sistemas (item 4 da fila aberta — resolvido).

As marcas de schedule são **schedules próprios** inseridos no
`MainScheduleOrder`, não sistemas com `before`/`after`: dentro de um schedule
o executor paralelo corre as âncoras quando quer. A primeira versão media
cada set do `PostUpdate` com um par `before`/`after` independente e dava seis
leituras cumulativas que somavam 27 ms num frame de 44 — a cadeia de UMA
marca resolve.

**O retrato do frame** (spawn do `simple-rpg`, relógio preso às 12:00,
RTX 4050 Laptop):

```
frame                 44,3 ms
├── main schedule       8,1 ms   (post_update 5,1 · update 2,1 · resto 0,9)
│    └── bevy.transform_propagate 4,1 ms  ← 58 k entidades
└── sched.render_wait  36,2 ms
     ├── render.prepare_views   11,8 ms
     ├── render.render          12,6 ms   (dos quais 7,4 ms de GPU nos passes)
     ├── render.bind_groups      1,5 ms
     └── prepare/assets/meshes   2,4 ms
```

Ou seja: **o frame é CPU no render app**, não GPU. A GPU mede 7,4 ms de
passes e passa o resto do tempo à espera.

**O achado.** A/B ao vivo (toggles novos do profiler `extra:dir-shadows` e
`extra:point-shadows`, braços interleaved na MESMA sessão):

| Braço | frame |
|---|---|
| sol + lanternas (como estava) | 44,5 ms |
| só lanternas (sem sol) | 44,0 ms |
| só sol (sem lanternas) | 31,4 ms |
| sem sombras nenhumas | 22,5 ms |

As sombras das **PointLight** valiam **~22 ms de 44** — metade do frame. Cada
lanterna com shadow map são **6 vistas** de render (as faces do cubo): cull,
fila, sort e batch de tudo o que lá cai, 12 vezes. O orçamento antigo dava
sombra às **12 mais próximas, fosse qual fosse a distância e a hora**: em
campo aberto isso eram 12 lanternas a centenas de metros, e ao meio-dia eram
12 lanternas cuja contribuição está 6 stops abaixo do sol.

**O fix** (`light_budget_system`, `src/ambient.rs`) mantém a política de LUZ
(todas iluminam) e aperta a de SOMBRA em três eixos:

1. **Ranking pela superfície da esfera de influência** (`distância − range`),
   não pelo centro: a lanterna de `range` 20 m a 300 m deixa de ser "a 12.ª
   mais próxima".
2. **Tecto de distância** `SHADOW_LIGHT_MAX_DISTANCE` = 60 m
   (`VIBER_SHADOW_LIGHT_DISTANCE`).
3. **Gate de luz do dia** `SHADOW_LIGHT_DAYLIGHT_MAX` = 0,35 da curva
   `daylight_factor` (a mesma do tint), com banda de 0,1
   (`VIBER_SHADOW_LIGHT_DAYLIGHT=1` desliga o gate).

A histerese vale nos dois eixos (rank e distância) — sem ela a lanterna
piscava ao andar na fronteira, com o refresh de 1 s.

**Medido** (relógio preso, spawn, duas corridas por braço no mesmo processo):

| Hora | shadow_lights | frame |
|---|---|---|
| 12:00 antes | 12 | 44,5 ms |
| **12:00 depois** | **0** | **30,0 / 30,0 ms** |
| 22:00 depois | 12 | 37,2 / 38,1 ms |

**De dia o frame cai 32 %** (44,5 → 30,0 ms, 22 → 33 fps) e à noite as
sombras das lanternas continuam todas lá, que é onde se veem. `VIBER_SHADOW_LIGHTS=<n>`
afina o orçamento (0 desliga) sem recompilar.

**Cascatas do sol** ganharam knobs (`VIBER_SHADOW_CASCADES`,
`VIBER_SHADOW_DISTANCE`) mas o default fica: 3 cascatas em vez de 4, ou
300 m em vez de 600, valem **1,9 ms** cada — não paga a perda de sombra nas
serras. Ficam para o perfil de GPU fraca.

**Nota de método:** com outro agente a correr uma engine na mesma GPU, os
braços entre BOOTS andaram ±100 % (um braço com `VIBER_WATER_SSR=0` chegou a
medir 61 ms contra 30 do baseline — contaminação, não regressão). Só valem os
braços **interleaved no mesmo processo**, e o `nvidia-smi
--query-compute-apps` antes de citar qualquer número.

### Ainda em aberto (por ordem de €/ms)

0. **`render.prepare_views` = 11,8 ms e `render.render` = 12,6 ms** são agora
   os dois maiores itens do frame, e ainda não estão atribuídos a um sistema
   concreto (o `PrepareViews` do Bevy leva o `prepare_lights`, as texturas de
   view do TAA/SSAO/bloom e a preparação do SSR; o `render` inclui o encode
   dos comandos e o present). O próximo passo é partir estes dois brackets em
   sistemas — as âncoras já existem, falta a granularidade.


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
