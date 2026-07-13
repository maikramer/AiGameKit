# Road

Estradas pintadas sobre o terreno, estilo city-builder: `<Road>` recebe uma
polyline em coordenadas de mundo, suaviza-a (Chaikin), constrói um ribbon que
segue a curva e conforma-se à superfície do terreno, e pinta a textura **ao
longo** da estrada — UV.v acumula arc-length e UV.u é a posição lateral (ambos
divididos por `texture-scale`), por isso a textura orienta-se, curva e nunca é
espremida, seja qual for a largura ou o traçado.

## Sintaxe

```html
<Road
  path="0 4  0 26  -6 44  -4 70"
  width="5.4"
  texture-scale="16"
  texture-url="/assets/textures/cobblestone_road.png"
  normal-map-url="/assets/textures/pbr_cobblestone_road/cobblestone_road_normal.png"
  edge-feather="1.1"
  edge-noise="0.45"
  end-feather-start="0"
  end-feather-end="3"
></Road>
```

## Atributos

| Atributo            | Default | Descrição                                                       |
| ------------------- | ------- | --------------------------------------------------------------- |
| `path`              | —       | Lista plana `x0 z0 x1 z1 ...` (mundo). ≥ 2 pontos.              |
| `width`             | `5`     | Largura total da faixa (m).                                      |
| `texture-scale`     | `16`    | Metros de mundo por tile de textura (u e v).                     |
| `edge-feather`      | `1.1`   | Fade lateral borda→núcleo (m).                                   |
| `edge-noise`        | `0.45`  | Ruído que corrói a borda para dentro (m). Determinístico.        |
| `end-feather-start` | `2`     | Fade na ponta inicial (m). `0` = sólida (enterrar sob uma praça).|
| `end-feather-end`   | `2`     | Fade na ponta final (m).                                         |
| `y-offset`          | `0.12`  | Elevação acima da superfície (m).                                |
| `station-spacing`   | `1.5`   | Espaçamento das estações do ribbon (m).                          |
| `smoothing`         | `2`     | Iterações Chaikin (0 = cantos vivos).                            |
| `flatten`           | `true`  | Aplaina um corredor no terreno (corte+aterro) ao longo do path; `flatten="0"` desliga. |
| `flatten-falloff`   | `6`     | Blend lateral do corredor de volta ao relevo natural (m).        |
| `flatten-window`    | `24`    | Janela da média móvel do perfil longitudinal (m). Tem de ser ≥ ao lattice do mesh base (worldSize/resolution) para as cordas dos LODs coincidirem com o perfil. |
| `opacity`           | `1`     | Opacidade global.                                                |
| `roughness`/`metalness` | `1`/`0` | PBR do material.                                           |
| `texture-url` / `normal-map-url` / `roughness-map-url` | — | Texturas (cache por URL). |

## Como funciona

- **Curvas**: o path é suavizado por Chaikin e reamostrado a `station-spacing`;
  cada estação usa a normal *miter* (média das normais dos segmentos vizinhos)
  para o offset lateral — curvas sem gaps nem vincos, e a textura acompanha
  porque o UV.v é o arco acumulado (mesma técnica do river do plugin water).
- **Bordas**: 4 vértices por estação (borda, núcleo, núcleo, borda) com alpha
  `[0,1,1,0]` no atributo `color` RGBA (vertex alpha nativo do three — **sem
  onBeforeCompile**, sobrevive ao patch de CSM). `edge-noise` corrói a borda
  para dentro com value-noise 1D ao longo do arco, lados independentes.
- **Terreno**: constrói depois de `TerrainPadApplySystem`, quando o heightmap
  está decodificado (`initialized && sampler.data` — antes disso o sampler é
  flat e a estrada ficaria enterrada) — cada vértice amostra a superfície LOD
  renderizada (`sampleMeshSurfaceHeight`), não a analítica. A altura por
  vértice é o **máximo** de uma vizinhança de meio-passo: o ribbon é plano
  entre estações e os triângulos do terreno têm ~15 m — sem isto, cristas
  convexas ("morrinhos") entre estações cortavam a estrada por cima
  (descontinuidade). Sem `<Terrain>` no mundo, constrói plano a y=0.
- **Junções**: `end-feather-*="0"` deixa a ponta sólida — estender o path até
  DENTRO do núcleo opaco de uma praça/`<Pad>` liga os dois sem costura
  translúcida.
- Decal puro: sem colisor, `castShadow=false`, `depthWrite=false`,
  `polygonOffset`. Cleanup por sidecar + `state.onDestroy` (eids reciclados).

## Carve (corredor)

`flatten="true"` aplaina um corredor no terreno ao longo do path, em ambas
as direções (corta morros e aterra vales), como estradas reais — corte e
aterro. O carve corre **dentro** do `RoadApplySystem`, **antes** do ribbon
amostrar a superfície: muta o `HeightSampler` do terreno (a fonte analítica
que todos consomem) e reconstrói as derivadas (chunks dirty, colliders,
BVH). O ribbon já amostra as alturas pós-carve no mesmo frame, porque
`sampleTerrainSurface` lê o sampler diretamente (não o mesh remeshed — o
remesh acontece no grupo `'draw'`, mais tarde no frame, mas é irrelevante
porque ninguém nesta cadeia lê o mesh, só o sampler).

Sem o carve, os chunks LOD grosseiros do terreno (~15 m por triângulo) fazem
"corda" por cima das depressões e nenhum offset fixo do ribbon cobre todos
os LODs — o "morrinho" que corta a estrada por cima. O carve resolve na
fonte: o corredor fica nivelado no sampler, todos os LODs renderizam a
estrada plana.

- **Perfil longitudinal**: média móvel triangular das alturas originais ao
  longo do arco do path, com janela `flatten-window` (default 24 m). Suaviza
  cristas/depressões pontuais sem achatar todo o relevo.
- **Falloff lateral**: dentro da `width` o terreno fica ao nível do perfil;
  entre `width` e `width + 2×flatten-falloff` há um smoothstep de volta ao
  relevo natural — transição sem degrau.
- **Density boost obrigatório**: o carve sozinho é INVISÍVEL — os vértices do
  mesh base distam `worldSize/resolution` (~15 m no simple-rpg) e um corredor
  de ~10 m cai entre eles. O sistema aplica boost 255 no DensityMap por
  segmento do path + `refreshChunkResolutions` (mesmo mecanismo dos
  lagos/rios); com o corredor esculpido os chunks próximos convergem para a
  superfície analítica do sampler.
- **Amostragem do ribbon**: com `flatten`, o ribbon amostra a superfície
  ANALÍTICA (`sampleHeightAt` + base Y), não o lattice base — é para o
  analítico que os chunks boosted convergem. Validação: clearance analítico
  ≥ +0.10 m em todas as estradas do simple-rpg, 0 vértices enterrados;
  segmentos distantes sob LOD grosseiro podem mergulhar temporariamente até
  o chunk refinar (mesmo trade-off da água).
- `flatten` é `1` (ON) por defeito. Com `flatten="0"`, a estrada é um decal
  puro sobre a superfície original (comportamento anterior).
- Mesmo contrato dos `<Pad>`/lagos/rios: muta o `sampler.data` in place,
  marca `TerrainChunk.meshDirty`, remove `chunkColliders`, invalida o BVH.

## Gotchas

- O path chega do XML como `number[]` (regra de vetores do XMLValueParser);
  o parser aceita também string para chamadas programáticas.
- `smoothing` corta cantos: o ponto autorado num canto vivo NÃO fica na curva
  final (fica a ~1/4 do vértice). Para passar exatamente por um ponto, usar
  `smoothing="0"` ou colocar pontos extra a ladear o alvo.
- Estradas que se cruzam: dois ribbons transparentes sobrepostos somam alpha
  nas zonas de feather — aceitável com a mesma textura; junções dedicadas
  (T/X com UV contínuo) ficam para uma iteração futura.
