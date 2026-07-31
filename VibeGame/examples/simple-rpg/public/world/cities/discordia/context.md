# cities/discordia — distritos

Shell: `../discordia.xml` (Includes). **Editar o XML do distrito**, não o shell.

| Ficheiro        | Group(s)                       | Conteúdo                                                                                                                                                                     |
| --------------- | ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `walls.xml`     | `city.walls`                   | **GERADO** (`scripts/gen_city_walls.py`). Muralha ±38 **GLB** (passo visual 6.38, junta 0.12): 4 corners + 40 segs + 4 portões + 8 tochas                                    |
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
QA visual pendente com `aigamekit-lab debug screenshot`).

## Shell contracts (`discordia.xml`)

| Item             | Valor                                              |
| ---------------- | -------------------------------------------------- |
| `SpawnExclusion` | `at="0 0" radius="52"` (sync `villageZones`)       |
| `TerrainPad`     | `size="120 120" falloff="20" corner-radius="18"`   |
| Gates            | Cardinal openings at wall ±38                      |
| Ruas             | anel ±25 + docks `mid_*` ±32 (`paths/network.xml`) |

### Layout compacto (muralha)

Semi-lado vem do **LOD0 visual** (collider ~12 cm mais longo; extremos da
malha também inset no AABB). `SEG=6.50`, `GATE=10`, `CORNER=2.43`, junta
visual `0.12` → `S=38`, `PITCH=6.38`, `overlap-max=0.3`. Regenerar:

```bash
python3 scripts/gen_city_walls.py > public/world/cities/discordia/walls.xml
```

Âncoras dos distritos escalaram ~0.62×, mas **os offsets dentro de cada
cluster não** — a chama da fogueira, o fumo da chaminé e as peças do pátio da
forja são geometria medida do GLB; escalá-los enterrava partículas na malha.
O pátio da forja foi a exceção: os offsets _frontais_ encolheram ~0.7× porque
a forja ficou mais perto do anel.

Edifícios movidos para libertar rua (verificado com um teste de distância
edifício↔polilinha das vias, não só com o analyze):

| Peça                  | Porquê                                                         |
| --------------------- | -------------------------------------------------------------- |
| `shepherd_cottage`    | 6.67 m de largo não cabe entre a artéria `mid_w` e a muralha   |
| `chapel`              | recuada 2 m — a via `mid_n`→`ring_ne` passava-lhe no telhado   |
| `well` / fogueira     | fora dos corredores de 4.8 m das artérias da praça             |
| `chest`               | fora do AABB rodado da longhouse (o analyze usa AABB, não OBB) |
| `weapon_rack`, barril | fora do nó `ring_sw`                                           |

`skirts.xml` sits **outside** the wall but still on/near the pad — natural clutter so the city does not read as a flat desert plateau. Valley resources stay in `../../spawn/ring.xml` (±58), not inside the exclusion disc.

City-layout recipes: engine `VibeGame/src/plugins/city-layout/context.md`.
Radii overview: [`../../context.md`](../../context.md).
