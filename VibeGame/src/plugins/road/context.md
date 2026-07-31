# Road

Estradas pintadas sobre o terreno, estilo city-builder: `<Road>` recebe uma
polyline em coordenadas de mundo, suaviza-a (Chaikin), constrói um ribbon que
segue a curva e conforma-se à superfície do terreno, e pinta a textura **ao
longo** da estrada — UV.v acumula arc-length e UV.u é a posição lateral (ambos
divididos por `texture-scale`).

Para uma **rede interligada** com larguras e cruzamentos programáticos, preferir
`<RoadNetwork>` (Ways + Segments) em vez de vários `<Road>` soltos a fundir.

## Sintaxe — Road

```html
<Road
  path="0 4  0 26  -6 44  -4 70"
  width="2"
  widths="2 2 2.4 2"
  texture-scale="16"
  texture-url="/assets/textures/cobblestone_road.png"
  normal-map-url="/assets/textures/pbr_cobblestone_road/cobblestone_road_normal.png"
  edge-feather="1.1"
  edge-noise="0.45"
  end-feather-start="0"
  end-feather-end="3"
></Road>
```

## Sintaxe — RoadNetwork

```html
<RoadNetwork
  default-profile="artery"
  default-width="2"
  crossing-flare="true"
  texture-url="/assets/textures/cobblestone_road.png"
  normal-map-url="..."
>
  <Way id="plaza" xz="0 0" width="2.4"></Way>
  <Way id="gate_e" xz="62 1"></Way>
  <Way id="desert_end" xz="145 -2"></Way>
  <Segment a="plaza" b="gate_e" via="3 0  32 0" profile="plaza"></Segment>
  <Segment a="gate_e" b="desert_end" via="80 3  100 5  120 4"></Segment>
</RoadNetwork>
```

- **Way**: `id` + `xz="x z"`; `width` opcional (diâmetro no nó).
- **Segment**: `a`/`b`; opcional `via="x z …"`, `width`, `profile="artery|spur|plaza|bridge"`,
  texture overrides; pontes: `bridge-url` (+ `bridge-collision-url`, `bridge-lod1-url`, `bridge-lod2-url`).
- **Profiles** (`profiles.ts`): defaults de flatten / edge / width / feathers.
  `bridge` = ribbon a `deckY`, flatten só nas aproches (não enche o canal).
- **`crossing-flare`** (default on): Ways grau ≥ 3 alargam ×1.45 nas pontas +
  patch de cruzamento (`makeJunctionGeometry`) + tip flare em `planRoadFusion`.
- Expand: **1 `<Road>` por Segment** com path densificado e `widths` lerped.
  Cadeias grau-2 → stitch; cruzamentos ≥3 tips → patch (não stamp end-to-end).

### Segment bridge (vão de rio)

```html
<Segment
  a="s_bank"
  b="s_resume"
  profile="bridge"
  bridge-url="/assets/meshes/river_bridge_wood_lod0.glb"
  bridge-collision-url="/assets/meshes/river_bridge_wood_collision.glb"
  bridge-lod1-url="/assets/meshes/river_bridge_wood_lod1.glb"
  bridge-lod2-url="/assets/meshes/river_bridge_wood_lod2.glb"
  bridge-native-span="18"
></Segment>
```

- Fecha o grafo (`pathToWay` / analyze).
- `RoadApplySystem` corre **depois** de `RiverApplySystem` (+ TerrainPad).
- Lip via `chooseBridgeLip`: **nunca raise** (`match-high` removido); gap →
  `match-low` (banco sólido mais baixo). Samples landward multi-depth +
  `pickSolidBankY` (ignora spike de artéria).
- Apply: artérias primeiro, pontes **por último** (lip vê banks pós-flatten).
- Ribbon Y = **topo do GLB** (`lip − embed + BRIDGE_RIBBON_CLEARANCE`), não o
  lip cru (senão cobble flutua ~10 cm acima da malha).
- Um regrade que baixa o lip tem de re-assentar o deck **antes** de pavimentar:
  `reseatBridgeDeckToLip` corre logo a seguir a `applyBridgeDeckHeights` dentro
  de `buildRoadGeometry`. Antes, o re-seat só acontecia em `spawnBridgeDeck`
  (chamado _depois_ da geometria) e o cobble ficava no lip antigo — ponte de
  pedra oeste: lip 37.19 → 36.85, ribbon 0.34 m acima da pedra.
- GLB centrado: entity em `lip−embed−BRIDGE_DECK_LOCAL_Y`, depois
  `seatBridgeDeckToLip` trava AABB max Y nesse plano.
- Collider trimesh **sem** `mesh-anchor: base`.
- Carve: só se texel menor que `BRIDGE_SKIP_CARVE_TEXEL_M` (10 m).
  `bridgeApproachCorridorOpts` **não** herda `flatten-falloff` largo da artéria
  (end-cap + `flatTargetY` tapava o vão quando Ways ≈ native span).
- Deck XZ: `bridgeDeckCenterXZ` = centreline do rio sob o vão
  (`river-crossing.ts`), não o mid dos Ways (pode ficar assimétrico).
- Ways: tips **fora** do mesh/carve da água, simétricos ao centro do rio.
- Into-span ≤ 2 m quando carve fino existe.
- Collider trimesh: scale **não-uniforme** (scaleX≠Y) — scaleX em Y criava
  hull fantasma acima do deck.
- Density + brush só nas stubs.
- Pontes **fora** do stitch end-to-end (evita leader a flattenar o canal).
- Analyze: warn se span/native ≪0.45 ou ≫2.75.

### Road × water (lake/river)

`RoadApply` corre **depois** dos carves de água. O stamp do leito usa
`mode=blend` (cut+fill); sem guarda, um flatten perto duma bacia **reenche** o
fundo e a superfície de água corta areia (“lago a vazar”).

- `waterPreserveZonesLocal` (`water-guard.ts`) → `noRaiseBelowY` +
  `preserveDiscs` / `preserveRibbons` na **waterline** (shore, não carve completo).
- Artérias: skip superfície molhada; bancos ficam stampáveis.
- Aproches de ponte: **sem** preserve discs (praia sob abutment precisa de
  terrace até ao lip); canal protegido por falloff curto + `noRaiseBelowY` +
  into-span pequeno.
- Trails `flatten="0"`: ribbon não deve terminar _dentro_ do raio do lago.

## Queries (runtime)

| API                              | Uso                                |
| -------------------------------- | ---------------------------------- |
| `onRoad(state, x, z)`            | ponto no corredor (brush registry) |
| `nearestRoad(state, x, z)`       | snap à centerline                  |
| `pathToWay(state, fromId, toId)` | BFS nos Ways da rede               |
| `wayPathPolyline(state, ids)`    | XZ world ao longo do path          |

`vibegame analyze` corre `checkRoadNetworks` (Ways órfãos, plaza↛tip gaps).

## Atributos `<Road>`

| Atributo                                               | Default | Descrição                                                                                          |
| ------------------------------------------------------ | ------- | -------------------------------------------------------------------------------------------------- |
| `path`                                                 | —       | Lista plana `x0 z0 x1 z1 ...` (mundo). ≥ 2 pontos.                                                 |
| `width`                                                | `2`     | Largura total da faixa (m). Sobrescrito pelo max de `widths` se presente.                          |
| `widths`                                               | —       | Opcional: um float por ponto do `path` (diâmetro variável).                                        |
| `texture-scale`                                        | `16`    | Metros de mundo por tile de textura (u e v).                                                       |
| `edge-feather`                                         | `1.1`   | Fade lateral borda→núcleo (m).                                                                     |
| `edge-noise`                                           | `0.45`  | Ruído que corrói a borda para dentro (m). Determinístico.                                          |
| `end-feather-start`                                    | `0`     | Fade na ponta inicial (m). Default sólido — fade+flatten = trincheira (pés).                       |
| `end-feather-end`                                      | `0`     | Fade na ponta final (m). Usar só sob pads/praças (`>0`).                                           |
| `y-offset`                                             | `0`     | Elevação acima do heightfield (m). Default 0 — CCT anda no sampler; `polygonOffset` evita z-fight. |
| `station-spacing`                                      | `0.35`  | Espaçamento base (m). + `densifyPathByHeight` onde o acorde erra o sampler.                        |
| `smoothing`                                            | `2`     | Iterações Chaikin (0 = cantos vivos).                                                              |
| `flatten`                                              | `true`  | Prepara o leito (terraço + sink) no heightfield antes do ribbon; `flatten="0"` = só decal.         |
| `flatten-falloff`                                      | `8`     | Ombro: blend lateral leito→relevo (m).                                                             |
| `flatten-window`                                       | `56`    | Janela do terraço longitudinal (m) — multi-pass; esmaga morrinhos no leito.                        |
| `flatten-max-grade`                                    | `0.22`  | Max \|Δh/Δs\| do perfil de projecto (~22%). `0` = sem limite.                                      |
| `opacity`                                              | `1`     | Opacidade global.                                                                                  |
| `roughness`/`metalness`                                | `1`/`0` | PBR do material.                                                                                   |
| `texture-url` / `normal-map-url` / `roughness-map-url` | —       | Texturas (cache por URL).                                                                          |

## Como funciona

- **Curvas**: Chaikin + reamostra a `station-spacing`; normal miter no offset
  lateral; UV.v = arco acumulado.
- **Bordas**: 4 verts/estação com vertex alpha; `edge-noise` 1D.
- **Terreno**: pipeline partilhado (`terrain/ground-mutation`): density
  (`applyCorridorDensity` + `densityLeafPad`) → `carveRoadCorridor`
  (`applyHeightBrush` + `nearestOnPolyline`) → `rebuildTerrainDerivatives`.
  Ribbon Y = lattice mesh na **centerline** (mesma que o collider do chunk)
  - seam lift para o nível 1× mais grosso, **cap `ROAD_LOD_CLEARANCE_MAX_M`
    = 0.06 m** + 0.04 de decal. O cap _é_ a flutuação em qualquer encosta
    longa (o lattice grosso corre 0.5–0.8 m acima) — com os antigos 0.3 m o
    herói andava 0.26–0.28 m abaixo da calçada em toda a subida oeste (pernas
    enterradas). **Nunca** max-neighborhood (stripes em dunas). Density força
    split até deepest leaf sob o corredor.
- **Fusão** (`junctions.ts`):
  - End-to-end (mesma textura, pontas ≤2.5 m, exactamente 2 tips no nó):
    `stitchEndToEndChains` — um ribbon + `widths[]` por vértice.
  - Cruzamento (≥3 tips) / T (`through`): dock sólido + taper; sem discos.
- Decal: sem colisor, `depthWrite=false` (true faz a estrada desaparecer no
  depth test), feather alpha, `polygonOffset`. Normais do heightfield
  (atributo); winding fica camera-facing — inverter winding para “consertar”
  `computeVertexNormals` culled o ribbon (`FrontSide`).

## Prep → pave

1. **`carveRoadCorridor`**: leito = `width + ROADBED_OVERHANG` (**2 m** total =
   **1 m** cada lado) + ombro `flatten-falloff` (8 m). Density boost + rebuild.
2. **Ribbon** no sampler já planado.

- **Perfil**: `designRoadProfile` = **3 passes** smooth + grade 0.22 +
  `platformSink` **0.12 m**.
- **Density boost**: mesh LOD grosso precisa densificar o corredor (igual
  lagos/rios/pads). Spawners: `meshSurfaceResolutionForPoint`.
- `flatten="0"`: só decal, sem mutar sampler.

## Gotchas

- `widths=` deve ter o mesmo número de valores que pontos em `path`.
- `smoothing` corta cantos — `smoothing="0"` para passar exacto num Way.
- Preferir `<RoadNetwork>` para artérias; ramos de terra/areia podem ficar
  `<Road flatten="0">` soltos.
- Rede tipo cidade: cruz + **anel** que partilha Ways nos braços (`mid_*`) —
  senão o anel fica órfão e só se vê um `+`. Simple-rpg: `paths/network.xml`.
- City-layout `<Street>` continua a emitir `<Road>` (não RoadNetwork).
