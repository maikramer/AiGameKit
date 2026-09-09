# CONTEXT.md — src/terrain

Sistema de terreno (100% volumétrico ✅): o `VoxelField` é a única fonte de
geometria — toda a grelha de chunks renderiza por transvoxel (células de
transição de LOD) com ladder de LOD por coluna, e o collider de terreno é o
trimesh dessa mesma superfície, um por coluna (`physics.rs`). O heightfield (PNG/.ahgt/procedural) sobrevive como INPUT:
termo-base do SDF, alvo do carve de features declarativas (`Terrain`,
`TerrainPad`, `Lake`, `River`, `Cliff`, `Road`, `RoadNetwork`) e base das
máscaras (CliffMask, splat, biomas).

## Como se articula

1. **Bootstrap one-shot** (`runtime.rs`): carrega/gera a heightmap → carve
   pads → água → estradas (pelo brush engine, com journal) → bandas de cliff
   3D + `CliffMask` → sharpen opt-in → **`<RockFeatures>` resolve** (semeia
   arcos/grutas/pontes contra o chão já carvado com o guard set COMPLETO —
   água, estradas, máscara, pads e discos das features autorais/irmãs;
   caves só no pé de encostas, com o path inteiro validado — e passa a specs
   normais) → mods do campo voxel → spawna as COLUNAS voxel dentro do raio
   de render, água e ribbons.
2. **Runtime** (`plugin.rs`): ladder de LOD por coluna (célula 1→2→4 m,
   histerese + gate anti-thrash), construção staged sob budget de caixas por
   frame, cull por `render-distance`, respawn à aproximação.
3. **Gameplay** lê sem tocar na mesh: `TerrainRuntime::sample /
   sample_mesh_surface / in_water / on_road` (recurso), `WaterBody`,
   `RoadPath` — é isto que o player, os spawners e os scripts usam. As
   queries leem o SDF (o transvoxel segue o zero do campo), nunca a mesh.
   Regra dura: gameplay com Y conhecido usa `surface_below`; `grid.sample`
   fora de `src/terrain/` é proibido.

## Estado

Estável pós-migração (commit "remove o heightfield 2.5D"). QA vivo:
simple-rpg 4 km a ~89–103 fps (689 colunas / 900 caixas), grutas/arcos do
qa-voxel com `ground_below` a dar 2 spans. Travessias em `qa-pontes.xml`:
`<Bridge>` (tabuleiro union — o collider da coluna traz o tabuleiro, ao
contrário do ribbon de `<Road profile="bridge">`, que não tem collider
nenhum e continua a não ter), `<Cave>` com `<Chamber>`/`<Shaft>` e raio por
perfil, `<Arch path spans profile>` e `<RockFeatures>`. Bench: caixa LOD0
32³ ≈ 5,8 ms, LOD2 16³ ≈ 1,6 ms; o segundo caso do bench mede o mesmo
trabalho com o campo cheio de mods e falha acima de 60 ms/caixa
(`cargo test --release --test chunk_build_bench`).

Armadilha conhecida fora da pasta: o `AGENTS.md` da raiz documenta que
larguras de estrada < 1.5 texéis viram no-op (promovidas por `min_effective`)
— mundos com estradas finixinhas podem parecer "não carved".

## Seals (saias de coluna) — a regra que já se pagou caro

A saia de fronteira de coluna (`seal_faces` no mesher) existe para tapar a
fenda entre anéis de LOD desiguais (o vizinho a 2/4 m desacorda da nossa
polilinha até a uma célula grossa), e já produziu o artefacto oposto:

1. **Só arestas com traverso horizontal selam.** Uma aresta quase vertical na
   fronteira é uma parede que continua abaixo — selá-la produzia triângulos
   degenerados (a, b e as cópias caídas colineares) e não tapava nada.
2. **"Sempre selar" é deliberado.** A parede selada fica enterrada no sólido
   quando o vizinho concorda; saber o LOD do vizinho no momento do rebuild
   acoplava colunas e forçava rebuilds em cascata. A parede invisível custa
   menos que o acoplamento.

Regressões: `test_seal_hangs_border_edges_down_without_touching_the_interior`,
`test_carved_boxes_have_no_degenerate_triangles` (teto honesto para os flaps
sub-voxel documentados).
