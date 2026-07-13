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
| `y-offset`          | `0.06`  | Elevação acima da superfície (m).                                |
| `station-spacing`   | `1.5`   | Espaçamento das estações do ribbon (m).                          |
| `smoothing`         | `2`     | Iterações Chaikin (0 = cantos vivos).                            |
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
- **Terreno**: constrói depois de `TerrainPadApplySystem`, quando
  `sampleTerrainSurface` responde (heightmap decodificado + pads/carves
  aplicados) — cada vértice amostra a superfície LOD renderizada
  (`sampleMeshSurfaceHeight`), não a analítica, para não flutuar (ver memória
  spawn-lod-anchor). Sem `<Terrain>` no mundo, constrói plano a y=0.
- **Junções**: `end-feather-*="0"` deixa a ponta sólida — estender o path até
  DENTRO do núcleo opaco de uma praça/`<Pad>` liga os dois sem costura
  translúcida.
- Decal puro: sem colisor, `castShadow=false`, `depthWrite=false`,
  `polygonOffset`. Cleanup por sidecar + `state.onDestroy` (eids reciclados).

## Gotchas

- O path chega do XML como `number[]` (regra de vetores do XMLValueParser);
  o parser aceita também string para chamadas programáticas.
- `smoothing` corta cantos: o ponto autorado num canto vivo NÃO fica na curva
  final (fica a ~1/4 do vértice). Para passar exatamente por um ponto, usar
  `smoothing="0"` ou colocar pontos extra a ladear o alvo.
- Estradas que se cruzam: dois ribbons transparentes sobrepostos somam alpha
  nas zonas de feather — aceitável com a mesma textura; junções dedicadas
  (T/X com UV contínuo) ficam para uma iteração futura.
