# vegetation/&lt;biome&gt;.xml — tapete + dossel por bioma

`<biome>` ∈ `forest` (N) · `desert` (E) · `swamp` (S) · `peaks` (O). Cada ficheiro
é um `<Group name="biome.<biome>.vegetation">` incluído antes do `landmarks/`
correspondente. Ver custo scenery vs colhível vs inimigo em `../context.md`
("Densidade — spawner instanciado vs entidade").

## forest.xml — Floresta Sombria (Norte)

Dono: `vegetation/forest.xml`.

| Camada                        | Mesh(es)                                                                           | `count`/densidade | Notas                                                                                                                                                  |
| ----------------------------- | ---------------------------------------------------------------------------------- | ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Tapete erva/planta/flor       | kit Quaternius `vegetation/*.glb`                                                  | 38000/km²         | **Desligado** (comentado) 2026-07-29 — pasta `/assets/meshes/vegetation/` não existe no disco; ver NOTE inline e `ASSETS_REGISTRY.md` §"GLBs em falta" |
| Dossel principal              | `pine_dark`                                                                        | 190               | mesh próprio deste bioma (não usar `tree_pine`, reservado aos Picos)                                                                                   |
| Mata rala de fundo            | `pine_dark` (lod1 como visual base)                                                | 120               | silhueta contra a serra da borda                                                                                                                       |
| Árvores mortas                | `dead_tree`                                                                        | 46                | quebram monotonia do dossel                                                                                                                            |
| Árvores colhíveis             | `pine_dark` + `pine_dark_stump_collision` + `tree.ts` + `ResourceNode kind="wood"` | 22                | única camada com collider+script — fica cara, ver tabela de custo                                                                                      |
| Sub-bosque cogumelos          | `mushroom_red`                                                                     | 70                | tufos densos, chão húmido                                                                                                                              |
| Pedregulhos musgosos (visual) | `moss_rock` / `rock_mossy`                                                         | 80                | sem collider                                                                                                                                           |
| Penedos com colisão           | `rock_mossy` (escala 3×)                                                           | 12                | poucos — alimentam bake do NavMesh                                                                                                                     |

**Kit de vegetação Quaternius ausente:** os 9 `.glb` (`grass*`, `plant_flat*`,
`flower_*`) referenciados no `<Vegetation>` do tapete nunca chegaram a ser
gerados/copiados para `/assets/meshes/vegetation/`. O elemento fica **comentado**
com uma NOTE inline em vez de apontar para caminhos mortos (isso faz
`vibegame analyze` falhar). Não existem GLBs de erva/planta/flor na raiz de
`/assets/meshes/` para substituir 1:1 — reativar só depois de regenerar o kit.

Depois de editar: `vibegame analyze examples/simple-rpg/index.html` → `errors=0`.
