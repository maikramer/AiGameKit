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

| Atributo                                               | Default | Descrição                                                                                          |
| ------------------------------------------------------ | ------- | -------------------------------------------------------------------------------------------------- |
| `path`                                                 | —       | Lista plana `x0 z0 x1 z1 ...` (mundo). ≥ 2 pontos.                                                 |
| `width`                                                | `5`     | Largura total da faixa (m).                                                                        |
| `texture-scale`                                        | `16`    | Metros de mundo por tile de textura (u e v).                                                       |
| `edge-feather`                                         | `1.1`   | Fade lateral borda→núcleo (m).                                                                     |
| `edge-noise`                                           | `0.45`  | Ruído que corrói a borda para dentro (m). Determinístico.                                          |
| `end-feather-start`                                    | `0`     | Fade na ponta inicial (m). Default sólido — fade+flatten = trincheira (pés).                       |
| `end-feather-end`                                      | `0`     | Fade na ponta final (m). Usar só sob pads/praças (`>0`).                                           |
| `y-offset`                                             | `0`     | Elevação acima do heightfield (m). Default 0 — CCT anda no sampler; `polygonOffset` evita z-fight. |
| `station-spacing`                                      | `0.35`  | Espaçamento base (m). + `densifyPathByHeight` onde o acorde erra o sampler.                        |
| `smoothing`                                            | `2`     | Iterações Chaikin (0 = cantos vivos).                                                              |
| `flatten`                                              | `true`  | Prepara o leito no heightfield (corte+aterro mínimo) antes do ribbon; `flatten="0"` = só decal.    |
| `flatten-falloff`                                      | `5`     | Ombro: blend lateral leito→relevo (m).                                                             |
| `flatten-window`                                       | `16`    | Suavização do perfil longitudinal (m).                                                             |
| `flatten-max-grade`                                    | `0.22`  | Max \|Δh/Δs\| do perfil de projecto (~22%). `0` = sem limite.                                      |
| `opacity`                                              | `1`     | Opacidade global.                                                                                  |
| `roughness`/`metalness`                                | `1`/`0` | PBR do material.                                                                                   |
| `texture-url` / `normal-map-url` / `roughness-map-url` | —       | Texturas (cache por URL).                                                                          |

## Como funciona

- **Curvas**: o path é suavizado por Chaikin e reamostrado a `station-spacing`;
  cada estação usa a normal _miter_ (média das normais dos segmentos vizinhos)
  para o offset lateral — curvas sem gaps nem vincos, e a textura acompanha
  porque o UV.v é o arco acumulado (mesma técnica do river do plugin water).
- **Bordas**: 4 vértices por estação (borda, núcleo, núcleo, borda) com alpha
  `[0,1,1,0]` no atributo `color` RGBA (vertex alpha nativo do three — **sem
  onBeforeCompile**, sobrevive ao patch de CSM). `edge-noise` corrói a borda
  para dentro com value-noise 1D ao longo do arco, lados independentes.
- **Terreno**: depois de `TerrainPadApplySystem` + heightmap pronto. Com
  `flatten`, fase A prepara o leito no sampler; fase B o ribbon amostra
  `sampleHeightAt` (igual CCT). Sem `<Terrain>`, plano a y=0.
- **Junções**: `end-feather-*="0"` deixa a ponta sólida — estender o path até
  DENTRO do núcleo opaco de uma praça/`<Pad>` liga os dois sem costura
  translúcida.
- Decal puro: sem colisor, `castShadow=false`, `alphaTest` + `depthWrite`,
  `polygonOffset`. Cross-section usa Y da linha de centro (não height lateral).
  Cleanup por sidecar + `state.onDestroy` (eids reciclados).

## Prep → pave (ordem real)

Como na vida real: **1) preparar o leito**, **2) pavimentar em cima**.

1. **`carveRoadCorridor`** (se `flatten=1`): muta o `HeightSampler` — leito
   da `width+1.5` (plataforma além da faixa) + ombro (`flatten-falloff`).
   Rebuild chunks / heightfields Rapier / BVH. Density boost no corredor
   (senão o mesh base nem amostra o leito).
2. **Ribbon**: `sampleHeightAt` no sampler **já planado** (mesmo que CCT).
   Cross-section partilha Y da linha de centro. Sem `y-offset` mágico.

Sem prep, LODs grosseiros fazem “corda” sobre depressões e o decal perde
para a areia / morrinhos.

- **Perfil**: smooth `flatten-window` 16 m + `flatten-max-grade` 0.22.
- **Leito**: faixa pintada + **1.5 m** de plataforma (`ROADBED_OVERHANG`);
  peso 1 no leito; smoothstep no ombro (`flatten-falloff` 5 m).
- **Density boost obrigatório**: o carve sozinho é INVISÍVEL — os vértices do
  mesh base distam `worldSize/resolution` (~31 m no simple-rpg a 2000/64) e um
  corredor de ~10 m cai entre eles. O sistema aplica boost 255 no DensityMap por
  segmento do path + `refreshChunkResolutions` (mesmo mecanismo dos
  lagos/rios/pads); com o corredor esculpido os chunks próximos convergem para a
  superfície analítica do sampler. Spawners/place têm de amostrar Y com
  `meshSurfaceResolutionForPoint` — senão props flutuam nas saídas cardeais
  (west/east) onde a estrada densifica o mesh e o lattice grosso ainda “vê”
  o planalto do pad.
- **Amostragem**: `sampleHeightAt` exacto (= mesh chunk = CCT). Densify por
  erro de acorde. **Nunca** `max(mesh)` (pés afundam).
- **Depth**: opaco + `alphaTest` + `depthWrite` (não `transparent`). Y da
  cross-section = centerline — height lateral nas dunas → z-fight/areia LOD.
  Tips: `end-feather=0` por defeito.
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
