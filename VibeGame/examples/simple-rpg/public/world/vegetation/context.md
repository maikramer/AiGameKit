# vegetation/&lt;biome&gt;.xml — tapete + dossel por bioma

`<biome>` ∈ `forest` (N) · `desert` (E) · `swamp` (S) · `peaks` (O). Cada ficheiro
é um `<Group name="biome.<biome>.vegetation">` incluído antes do `landmarks/`
correspondente. Ver custo scenery vs colhível vs inimigo em `../context.md`
("Densidade — spawner instanciado vs entidade").

## Densidade (pós-expansão do mapa)

- Spawners `count=` (árvores, rochas, foliage props, inimigos em `creatures/`) → **×2**
- Tapete `<Vegetation>` erva/planta/flor → **~10000 instâncias por bioma**
  (`density-per-km2` calibrada pela área da região do tapete)
- Kit bpy: `npm run generate-vegetation` → `public/assets/meshes/vegetation/*.glb`
- Engine soft-cap: ≤12000 instâncias por camada `foliage` (density mode) — evita
  `pendingKick` de dezenas de milhares a travar o loading gate. As densidades
  ficam abaixo do cap (sem capping silencioso) e o boot fica em ~86% do
  orçamento de 100k entidades (o aviso dispara a 90%).

| Bioma  | `density-per-km2` (erva) | ≈ instâncias |
| ------ | ------------------------ | ------------ |
| forest | 99000                    | 9932         |
| desert | 103000                   | 9942         |
| swamp  | 91000                    | 9974         |
| peaks  | 103000                   | 9942         |

## forest.xml — Floresta Sombria (Norte)

Dono: `vegetation/forest.xml`.

| Camada                        | Mesh(es)                                                                           | `count`/densidade | Notas                                |
| ----------------------------- | ---------------------------------------------------------------------------------- | ----------------- | ------------------------------------ |
| Tapete erva/planta/flor       | kit bpy `vegetation/*.glb`                                                         | 99000/km²         | ativo; `npm run generate-vegetation` |
| Dossel principal              | `pine_dark`                                                                        | 380               | ×2                                   |
| Mata rala de fundo            | `pine_dark` (lod1 como visual base)                                                | 240               | ×2                                   |
| Árvores mortas                | `dead_tree`                                                                        | 92                | ×2                                   |
| Árvores colhíveis             | `pine_dark` + `pine_dark_stump_collision` + `tree.ts` + `ResourceNode kind="wood"` | 44                | collider+script — cara               |
| Sub-bosque cogumelos          | `mushroom_red`                                                                     | 140               | ×2                                   |
| Pedregulhos musgosos (visual) | `moss_rock` / `rock_mossy`                                                         | 160               | ×2                                   |
| Penedos com colisão           | `rock_mossy` (escala 3×)                                                           | 24                | NavMesh bake                         |

Depois de editar: `vibegame analyze examples/simple-rpg/index.html`.
