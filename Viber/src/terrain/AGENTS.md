# AGENTS.md — src/terrain

Escopo: terreno **100% volumétrico** declarativo — heightfield como INPUT
(termo-base do SDF), carve de features (pads/lagos/rios/estradas), colunas
voxel meshados por **transvoxel** (marching cubes com células de transição
de LOD) + ladder de LOD por coluna, collider trimesh POR COLUNA (`physics.rs`).
Contrato XML completo: `AGENTS.md` da raiz.

## Modelo de dados (o contrato que define tudo)

- **O `VoxelField` é a única fonte de geometria.** `density(p) =
  p.y − grid.sample(p.xz)` combinado em CSG com os mods
  (`min` = union, `max(d, −m)` = subtract; **negativo = sólido**). O
  `BrushGrid` não é renderizado nem colide directamente — é o TERMO-BASE do
  SDF e o alvo dos carves.
- **Heightmap continua a ser input:** PNG 8/16-bit / `.ahgt` / procedural FBM.
  Mundos existentes mantêm a silhueta; o que morreu foi o heightfield como
  mesh/collider/LOD (2.5D), não como dado.
- **A superfície desenhada É o zero do SDF.** `sample_mesh_surface` devolve
  sempre `voxel.surface_top` (bisseccionado, ~0,12 mm); o transvoxel segue
  o campo a precisão sub-voxel, e o LOD só muda o tamanho da célula.
- **`sample(x, z)` continua a ser a superfície MAIS ALTA** (o telhado do
  arco, não o lado de baixo). Quem precisa de estar por baixo chama
  `surface_below(x, z, from_y)` — devolve `None` quando não há chão.

## Ficheiros

| Ficheiro | Responsabilidade |
|----------|------------------|
| `spec.rs` | contrato XML: `TerrainSpec`, `TerrainPadSpec`, tint. `resolution` = célula do LOD0 voxel (`chunk_size/resolution`, 1 m no default); `collision-resolution` = interruptor (`0` desliga os colliders; já não define geometria); `levels`/`lod-distance-ratio`/`lod-hysteresis`/`render-distance` alimentam o ladder de colunas |
| `sampler.rs` | `HeightSampler` + guard `apply_pads` (só para testes) |
| `heightmap.rs` | PNG 8/16-bit ou `.ahgt` (header JSON + grid u16 deflate); sem ficheiro → procedural determinístico via `seed` |
| `brush.rs` | brush engine: modos blend/lower/raise, **journal por owner** (`pad:0`, `road:3`…) com revert para re-carve idempotente, `min_effective` (larguras < 1.5 texéis promovidas). O produção é deliberadamente **unguarded** |
| `features.rs` | **ordem de carve:** Pads → Lakes → Rivers → Roads (arteriais primeiro, **pontes por último**); estradas saltam núcleos de pads e zonas de água. Cliffs não carvam — são sólidos voxel |
| `cliffs.rs` | `CliffSpec` (parser) + **`CliffMask`** — scan de declive → abertura morfológica → BFS → filtro regional (área/queda/extent) → camadas core/dilatada + wall space (canal R). Consumidores: splat (pedra no core), relva/spawners (exclusão), shader (gate triplanar). `sharpen_terrain` (opt-in): terraceia rampas > `sharpen-angle` do campo FINAL dentro do core da máscara |
| `water.rs` | `LakeSpec`/`RiverSpec` + `WaterBody` (queries `contains`/`is_near`/`surface_y_at`/`distance_to_waterline`); lake = contorno orgânico lower-only; river = Chaikin + prefixo-mínimo (água nunca sobe). **Quedas**: o scan contíguo produz `CascadeInfo { lip, base, drop, waterfall, wall, top_y, bot_y }` — queda ≥ `waterfall_min_drop` é CACHOEIRA (cortina ×`WATERFALL_CURTAIN`, caldeirão ∝ queda); `river_cliff_crossings` cruza rio×cliff em specs (2D) e `carve_river_with_falls` conduz o perfil pelo cruzamento (hold a montante + queda garantida) |
| `splat.rs` | **blend de solo** (`layers="…"`): gerador puro do splat map por chunk (top-4 slots), leito forçado a `pebbles`. Aliases → `/assets/textures/<alias>/albedo.ktx2` |
| `layer_material.rs` | `TerrainChunkMaterial` bindless (4 texturas + splat por chunk), shader `chunk.wgsl` reescrito por mundo; `terrain_daynight_tint` |
| `water_material.rs` / `water_fx.rs` | `WaterMaterial` (Beer–Lambert, fresnel, glint) + splash/esteira |
| `roads.rs` | `RoadSpec`/`RoadNetworkSpec` + `RoadPath`; profiles, flatten com teto de grade, ribbons + discos de junção |
| `decal.rs` | `GroundDecalSpec` + `ground_decal_mesh` — manchas de chão drapejadas, só visuais |
| `voxel/` | **a forma 3D inteira.** `field.rs` (`VoxelField` SDF: `surface_top`/`surface_below`/`column`/`region_state`), `mods.rs` (trait `VoxelMod` + primitivas `BoxMod`/`CapsuleMod`/`OrientedBoxMod`/`RoundConeMod`/`EllipsoidMod`/`ArchMod`, mais os helpers partilhados `yaw_local`/`box_distance`/`yawed_bounds`), `index.rs` (`ModIndex` bucket XZ O(1)), `cliff.rs` (`<Cliff>` 3D — `CliffBand` + `profile_offset` negativo = undercut), `cave.rs` (`<Cave>` cápsulas/cones subtractivos, `<Chamber>`, `<Shaft>`, perfil de raio por comprimento de arco), `arch.rs` (`<Arch>` união com vão; `at` ou `path`, `spans`, `profile=portal|natural`), `bridge.rs` (`<Bridge>` — travessia 100% aditiva: tabuleiro, pilares, intradorso e tímpano, ou span natural em cones), `scatter.rs` (`<RockFeatures>` — semeia arcos/grutas/pontes e resolve em specs normais; guards ao sítio E ao path inteiro: água/estradas/CliffMask/pads/discos de features já no mundo, caves só no pé de encostas com `min-rise`, contabilidade de rejeições em `ScatterStats`), `riverbank.rs` (margens gorge/overhang + nascente + `wall_waterfalls` — anota quedas rio×cliff como `wall` e emite a fenda de spill no brow), `transvoxel_mesh.rs` (marching cubes com células de transição — costura de LOD sem saias), `spawn.rs` (`lod_shape`/`column_boxes`/`build_box_mesh`/spawn de colunas) |
| `paths.rs` | `chaikin_smooth` / `resample` |
| `mesh.rs` | infraestrutura partilhada: `HeightField` (termo-base), `ChunkMeshData`, `TintParams`/`tint_vertex_color` (tint legado + canais R/A de parede no caminho layers) |
| `plugin.rs` | `TerrainPlugin`: ladder de COLUNAS voxel — adopt → select com histerese → construção staged sob budget de CAIXAS (4/frame) → swap atómico → cull por `render-distance` → respawn no LOD cru |
| `runtime.rs` | `TerrainFeaturesPlugin`: bootstrap one-shot (heightmap → carve → mods → colunas voxel → água/ribbons); gera splats + materiais e publica `TerrainChunkMaterials`/`TerrainRuntime` |

## Regras duras (não quebrar)

- **Sampler CPU único:** nenhuma segunda fonte de verdade de altura. O
  `BrushGrid` é o base(p) do SDF; numa coluna sem mod o campo É o heightfield
  (mesmo valor, custo de Catmull-Rom).
- **Visual do chão, dois caminhos exclusivos:** tint LEGADO por
  altura/inclinação em vertex colors OU o splat blend (`layers="…"`), que
  SUBSTITUI o tint — `TerrainSpec::chunk_tint()` zera o banding com camadas
  ativas, no bootstrap E nos rebuilds (desalinhados, caixas vizinhas
  discutem). Com `layers`, R das vertex colors = wall space e A = cliff
  factor; no legado, RGBA = tint integral (o StandardMaterial multiplica).
- **O collider É a mesh: um trimesh por COLUNA** (`physics.rs::ColumnColliderBake`):
  os triângulos são exatamente os que o transvoxel desenha — acumulados no
  staging e assados no swap atómico do LOD (mesh e collider trocam no mesmo
  frame), com `FIX_INTERNAL_EDGES` (pseudo-normais, sem ghost collision nas
  arestas internas). Vive na entidade da coluna; `collision-resolution` é só
  interruptor (`0` = sem colliders). Banda de colisão
  `min(chunk×3, lod_distance)` com PISO de LOD 0 dentro dela (câmara OU
  herói): o chão tocável é sempre geometria fina e nunca troca de LOD sob o
  herói. `stream_voxel_colliders` mantém add/repair/remove e publica
  `TerrainCollisionStatus` — o player só usa o chão analítico quando não há
  collider carregado; com chão carregado, o collider é a autoridade.
- **Uma travessia é um sólido, não um ribbon.** `<Bridge>` vive no campo
  voxel, portanto o trimesh da coluna traz o tabuleiro e o herói anda por
  cima sem uma linha de código de colisão nova. `<Road profile="bridge">`
  continua a ser só um decal em `deck_y` — desenhado, sem collider. Não
  confundir os dois, e não "corrigir" o segundo com geometria: quem quer uma
  ponte de estrada atravessável põe um `<Bridge>` por baixo do segmento.
- **Uma travessia é ADITIVA, ponto.** Nenhum mod de `<Bridge>` é subtractivo,
  e é por isso que nenhuma forma de ponte pode danificar o terreno que
  atravessa. A construção óbvia — encher tudo sob o tabuleiro e subtrair as
  arcadas — não sobrevive a um desfiladeiro em V: o vão atravessa a garganta,
  as paredes da garganta sobem para dentro dele, e a subtração come a rocha
  onde a ponte assenta. Pilares + intradorso + tímpano, tudo union.
- **Um arco só abre onde há vão a abrir.** O troço de um span é o que tem o
  tabuleiro mesmo livre do chão; dimensionar a abertura pelo comprimento do
  tabuleiro punha arcadas no meio da margem em qualquer ponte mais longa que
  a sua garganta. E a flecha vem primeiro, a nascença sai dela: nascer do topo
  dos pilares soa certo e não é — as pontas do troço livre estão a folga zero,
  e o arco saía plano.
- **Perfis ao longo de um caminho keiam-se por COMPRIMENTO DE ARCO**, nunca
  por índice de estação: o `resample` prega o ponto final autorado, portanto a
  última estação é um toco e o índice desloca o meio de um `radius="2 6 2"` ou
  o ápice da camber de uma ponte.
- **`ModIndex` afina a célula sozinho** acima de `REFINE_ABOVE_MODS` mods
  (piso 8 m, teto 256 células por aresta). Sem isso as ~40 caixas de um
  tabuleiro caem todas num bucket de 64 m e cada uma das ~39 k amostras de
  uma caixa LOD0 paga as quarenta — medido a 3,5× (`docs/PERFORMANCE.md`).
- **`<RockFeatures>` resolve em specs normais**, no bootstrap, contra o chão
  já carvado. Não é um tipo de sólido novo: se algo a jusante precisasse de
  saber que uma gruta foi semeada em vez de escrita à mão, o desenho estava
  errado.
- Todo o mutate passa pelo brush engine com journal — carve tem de ser
  **idempotente**.
- **Decals são só visuais** — `GroundDecal` nunca toca no heightfield.
- **Camadas transparentes de chão têm ordem fixa:** `DECAL_LIFT` (0.04) <
  `RIBBON_LIFT` (0.06) < `JUNCTION_LIFT` (0.10).
- **Nada de decal transparente a projetar sombra** (`NotShadowCaster`); a
  água sob a mesma regra.
- **Contrato de vértice da água** (`water.rs` ↔ `water.wgsl`): `COLOR.rgb` =
  cor do corpo, `COLOR.a` = máscara de margem, `UV.x` = escala de extinção,
  `UV.y` = radial/transversal.
- **A opacidade da água NÃO é um alpha constante** (depth prepass →
  Beer–Lambert); **fresnel com normal quase plana**; hash de ruído INTEIRO
  (u32) em WGSL world-space; FBM com rotação por oitava.
- **`sharpen` é opt-in e nunca default** — muda alturas (colisão, spawns,
  quests movem-se).
- **WGSL do muro: derivadas só em fluxo uniforme**; `textureSampleGrad` com
  gradientes calculados uma vez no topo do fragment.

## Colunas voxel — regras duras

- **Ladder por coluna, não por caixa:** `lod_shape(lod0_cell, edge, lod)`
  deriva `(cells, per_edge)` — LOD0 2×2 caixas de 32³ @1 m, LOD1 1×64³ @2 m,
  LOD2 16³ @4 m (chunk 64 m). Determinístico e global: vizinhos ao mesmo LOD
  derivam o MESMO shape — é isso que fecha costuras por coincidência de
  vértices.
- **`region_state` antes de amostrar** — céu/bedrock provados em O(1) pelo
  `range_over` da grid; sem isso uma coluna de 200 m de relevo custa ~1 M
  avaliações.
- **Construção staged:** as caixas do novo LOD nascem `Visibility::Hidden`
  sob o budget; quando a fila esvazia, as velhas morrem e as novas ficam
  visíveis — troca atómica, nunca dois LODs do mesmo chão nem buraco.
- **Seals de fronteira de coluna:** saia vertical (`seal_depth =
  max(4×célula, 2 m)`) nas faces ±X/±Z na fronteira, SÓ em arestas com
  traverso horizontal. "Sempre selar" é deliberado (parede enterrada quando
  o vizinho concorda; saber o LOD do vizinho acoplava colunas).
- **As caixas são iteradas a partir da grelha de chunks de terreno**, não de
  uma grelha voxel própria — as origens não coincidem num mundo de 4 km
  (−2000 não é múltiplo de 32 m); transbordar é z-fighting.
- **Material por coluna do `ChunkLayerMap`**; o standard fallback é
  double-sided (folhas finas sub-voxel com culling leem-se como buracos).
- **Despawn de coluna é recursivo** (Bevy 0.19): as caixas morrem com ela no
  cull.
- **Normais vêm do gradiente do campo** — contínuas através das fronteiras,
  sem estado partilhado entre builds.
- **Flaps sub-voxel conhecidos:** folhas finas (lips de carve a centímetros)
  produzem ~0,4% de triângulos degenerados num mundo carvado — teto honesto
  em `test_carved_boxes_have_no_degenerate_triangles`; o fix real é
  refinamento de voxel perto de features, não mesher novo.

## Desvios conhecidos vs VibeGame (documentados, não afetam o simple-rpg)

Estações de road a 1 m (vs 0.35); sem berms/cross-slope; decks de
`<Road profile="bridge">` continuam ribbons planas sem collider (GLB chega
com glTF). A travessia atravessável é a tag `<Bridge>`, que é sólido voxel —
ligar o `profile="bridge"` a esse sólido fica por fazer, e é decisão
deliberada.

## Verificar

```bash
cd Viber && cargo test          # inclui tests/terrain_mesh_health.rs (voxel)
cargo test --release --test chunk_build_bench -- --nocapture
cargo run -- analyze worlds/terrain.xml    # mundo demo de terreno
cargo run -- analyze worlds/qa-voxel.xml   # grutas, arcos, overhangs
cargo run -- analyze worlds/qa-pontes.xml  # travessias, salas, viaduto, dispersão
```
