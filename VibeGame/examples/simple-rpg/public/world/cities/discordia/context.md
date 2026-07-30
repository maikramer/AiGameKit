# cities/discordia — distritos

Shell: `../discordia.xml` (Includes). **Editar o XML do distrito**, não o shell.

| Ficheiro        | Group(s)                       | Conteúdo                                                                                                                                                                     |
| --------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `walls.xml`     | `city.walls`                   | Muralha ±32 **GLB** (`city_wall_corner` ×4 + `city_wall_seg_{a,b}` ≈28A/4B), 4 portões **GLB** `city_gate_arch` + `torch_post`                                               |
| `roads.xml`     | `city.roads`                   | Pad praça (artérias em `paths/network.xml`)                                                                                                                                  |
| `utilities.xml` | `city.plaza`, `city.landmarks` | Poço **GLB**, fogueira **GLB** `campfire_pit` + brasa/luz, bancos **GLB**, quadro **GLB** `notice_board`, tochas **GLB** `torch_post` + brasa/luz (2 PointLight), santuários |
| `houses.xml`    | `city.houses`                  | 4 casas **GLB**: 3× `village_house` (A/B/C) + 1× `shepherd_cottage` (Casa do Pastor); barril GLB junto à casa B                                                              |
| `forge.xml`     | `city.forge`                   | Ferraria **GLB** `village_forge` + brasa/fumo (partículas), `anvil`/`horseshoe_pile`/`quench_trough`/`forge_bellows`/`weapon_rack` **GLB** no pátio, ferreiro (NPC)          |
| `barn.xml`      | `city.barn`                    | Celeiro **GLB** `village_barn`                                                                                                                                               |
| `watch.xml`     | `city.watch`                   | Torre de vigia (GLB)                                                                                                                                                         |
| `chapel.xml`    | `city.chapel`                  | Capela (GLB)                                                                                                                                                                 |
| `market.xml`    | `city.market`                  | Mercado (bancas GLB); `merchant` já renderiza `npc_merchant` via script próprio (`src/scripts/merchant.ts`), sem `<GLTFLoader>` declarativo                                  |
| `longhouse.xml` | `city.longhouse`               | Longhouse                                                                                                                                                                    |
| `skirts.xml`    | `city.skirts`                  | Vazio — vegetação periurbana nos spawners `vegetation/*.xml`                                                                                                                 |
| `grid.xml`      | `city.grid-district`           | Stub vazio (CityGrid→Box removido; foco GLB)                                                                                                                                 |

### Onda composition — migrado (2026-07-29)

`walls.xml`, `utilities.xml`, `houses.xml`, `forge.xml`, `barn.xml` deixaram de
usar `<Composition>` de primitivas para os edifícios/props principais —
agora usam `<GameObject>` + `<GLTFLoader>` (mesh-url `..._lodN.glb`) com
`collider="shape: trimesh; mesh-url: ..._collision.glb; mesh-anchor: base"`.
Rotação/posição herdadas das antigas Composition (porta/frente já viradas
para a praça ou via). Efeitos dinâmicos (brasa, fumo, luz de tocha/fogueira)
ficam como `<Composition>`/`<ParticleSystem>` finos por cima do mesh estático
— nunca bakeados na malha. Posições de fumo/brasa da forja são aproximadas
(chaminé/hearth reais do GLB podem divergir ligeiramente da caixa antiga;
QA visual pendente com `gamedev-lab debug screenshot`).

## Shell contracts (`discordia.xml`)

| Item             | Valor                                          |
| ---------------- | ---------------------------------------------- |
| `SpawnExclusion` | `at="0 0" radius="42"` (sync `villageZones`)   |
| `TerrainPad`     | `size="96 96" falloff="16" corner-radius="14"` |
| Gates            | Cardinal openings at wall ±32                  |

`skirts.xml` sits **outside** the wall but still on/near the pad — natural clutter so the city does not read as a flat desert plateau. Valley resources stay in `../../spawn/ring.xml` (±58), not inside the exclusion disc.

City-layout recipes: engine `VibeGame/src/plugins/city-layout/context.md`.
Radii overview: [`../../context.md`](../../context.md).
