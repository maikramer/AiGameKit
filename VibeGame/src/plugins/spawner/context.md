# Spawner (terreno)

Spawn procedural declarativo com `<SpawnGroup>` no `index.html`, alinhado à altura do terreno e opcionalmente à **inclinação** (normal por diferenças finitas no heightmap).

## Chão: estáticos vs criaturas (honestidade)

### Estáticos (`StaticSpawner` / place / tree / foliage)

1. `isGroundMutationPending` — defer até pad / lago / rio / `Road flatten`.
2. `spawnTemplateAtTerrain` — mesh lattice + opcional `ground-align=aabb` (foot plant).
3. `TerrainSpawned` + resync / AABB catch-up.

### Criaturas (`DynamicSpawner`, `role=enemy`)

XML típico = `<Creature>` + `GLTFLoader`: kinematic + capsule + `CharacterController` (recipe em `physics/recipes.ts`). Rapier CCT planta Y no heightfield do chunk (mesma autoridade que o player). NavMesh com CCT = só `CharacterMovement.desiredVel` (não escreve `Transform` XZ). Spawn seeds `Transform.posY` uma vez na amostra de superfície — sem AABB lift, sem `TerrainSpawned`, sem snap no `creature.ts`. `goblin_collision.glb` em disco continua **não referenciado** (CCT usa capsule).

| Perfil / role                            | Path de chão                                       |
| ---------------------------------------- | -------------------------------------------------- |
| `tree` / `foliage`                       | AABB + align estático (resync)                     |
| `creature` (`role=enemy\|npc\|creature`) | CCT no heightfield; NavMesh → desiredVel           |
| `physics-box` / `gltf-crate`             | `ground-align=aabb`, `base-y-offset=0` (estáticos) |

**Anti-padrões (não reintroduzir):**

- Snap de Y no script (`applyTerrainSpawnedY`, BVH, `settleOnGround`) a fingir física.
- Footprint-max / lifts AABB em agents sem Rigidbody.
- `role=enemy` → `physics-box`.
- Slope sink / magic `base-y-offset` a tapar pivô errado do GLB.

## `<GameObject place="…">` — posicionamento determinístico (recomendado)

Para colocar props, NPCs, partículas ou qualquer recipe num **ponto fixo** sem adivinhar `Y` manualmente, use **`<GameObject place="at: x z; …">`**. O atributo `place` é uma string com pares `chave: valor` separados por `;` (estilo semelhante ao `transform`). O motor amostra a superfície do terreno nesse XZ, posiciona a **entidade raiz**, aplica `base-y-offset` / `y-offset`, alinhamento à normal (`align-to-terrain`) e, para GLBs com URL nos filhos, `ground-align="aabb"` (base do modelo no chão). Os **filhos** (`GLTFLoader`, `ParticleSystem`, `<NPC>` com `merge`, etc.) ficam na hierarquia ECS sob essa raiz.

- **Evite** `pos="x y z"` em objetos que devem assentar no terreno procedural: o `Y` fixo tende a enterrar ou flutuar.
- **Prefira** `<GameObject place="…">` para um único ponto; use `<SpawnGroup>` para **várias instâncias aleatórias** numa região.

Chaves típicas dentro de `place` (ver também `place-fields.ts` e `profiles.ts`, perfil interno `place`):

| Chave              | Significado                                                                                                    |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| `at` (obrigatório) | Dois números `x z` — posição horizontal em mundo (e âncora do grupo; soma-se ao `transform` do pai se existir) |
| `y-offset`         | Atalho para `base-y-offset` (offset vertical após o solo)                                                      |
| `ground-align`     | `aabb` \| `none` — elevar GLB pelo AABB local (relevante com `url` num filho)                                  |
| `align-to-terrain` | `1` \| `0` — rodar a raiz para alinhar à normal do terreno                                                     |
| `max-slope-deg`    | Inclinação máxima aceite; acima, aviso e instâncias omitidas                                                   |

Atributo irmão (fora de `place=`): `overlap-max="0.1"` — só `vibegame analyze`; tolera penetração XZ até N metros (`min(Δx,Δz)`). Default ausente = 0 (estrito). Também em `<Composition>` / `<Creature>`.

O perfil interno `place` em `profiles.ts` define defaults para esse modo: `align-to-terrain=1`, `ground-align=aabb`, escala 1, sem yaw aleatório, `max-slope-deg=90`.

Ficheiros: `entity-parser.ts`, `place-fields.ts`, `place-system.ts` (`TerrainPlaceSystem`), `place-context.ts`, `place-types.ts`, `spawn-template.ts` (lógica partilhada com o spawn aleatório).

**Ordem de sistemas:** `TerrainPlaceSystem` corre no **primeiro** bucket de `simulation` (`first: true`), **antes** de `TransformHierarchySystem`. Assim o `Transform` da raiz já reflete o solo quando os filhos recebem `WorldTransform`. Se o placement viesse **depois** da hierarquia, filhos (ex. `ParticleSystem`) ficariam com mundo errado até ao frame seguinte — em XZ perto de `(0,0)` e altura inconsistente.

### Exemplo mínimo

```xml
<GameObject place="at: -4 6; base-y-offset: 0.02">
  <GLTFLoader role="visual" url="/assets/models/prop.glb" transform="scale: 1 1 1"></GLTFLoader>
  <ParticleSystem preset="sparks" rate="4" transform="pos: 0 0.5 0"></ParticleSystem>
</GameObject>
```

NPC com merge no pai:

```xml
<GameObject place="at: 12 8; align-to-terrain: 0; ground-align: none; y-offset: 0.44">
  <NPC behavior="wander" max-speed="1.1" max-force="3.5"></NPC>
</GameObject>
```

`<NPC>` vem do plugin `ai-yuka` (DefaultPlugins). Guia: [`docs/AI.md`](../../../docs/AI.md).

## Layout

- `plugin.ts` — `SpawnerPlugin` (recipe, parser, system, defaults)
- `parser.ts` — lê atributos e filhos como templates de recipe (`SpawnGroup`)
- `entity-parser.ts` — lê `place` em `<GameObject>` e regista `PlacementSpec`
- `systems.ts` — `TerrainSpawnSystem` (**setup**, depois pad/lake/river/road — ver abaixo)
- `place-system.ts` — `TerrainPlaceSystem` (posiciona raiz e/ou instancia templates legados de spec)
- `surface.ts` — `sampleTerrainSurface`, `isGroundMutationPending`, normais/declive
- `occupancy.ts` — footprints + `SpawnExclusion`
- `spawn-template.ts` — `spawnTemplateAtTerrain` (spawn único no solo; suporta template `<GameObject>` com filhos)
- `transform-merge.ts` — parse/merge de `transform` e `composeSpawnRotation` (quaternions)
- `context.ts` — `WeakMap` State → spec por entidade (`SpawnGroup`)
- `place-context.ts` — idem para colocação determinística (`entity` com `place`)
- `profiles.ts` — perfis `profile` no grupo/filhos e merge de defaults

## Perfis (`profile`)

Atributo **`profile`** no `<SpawnGroup>` (e opcionalmente no **filho**) preenche defaults quando o atributo correspondente **não** aparece no XML. Valores explícitos **sempre** prevalecem.

### `<SpawnGroup profile="...">`

| profile           | Descrição                                            | Defaults (se omitido no XML)                                                                                                                                         |
| ----------------- | ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `none` ou omitido | Legado                                               | `align-to-terrain=0`, `base-y-offset=0`, `ground-align=none`, `random-yaw=0`, `scale-min/max=1`, `surface-epsilon=0.75`, `max-slope-deg=45`, `max-slope-attempts=32` |
| `tree`            | Vegetação GLB                                        | `align-to-terrain=1`, `ground-align=aabb`, `base-y-offset=0`, yaw aleatório, `scale-min≈0.7` / `scale-max≈1.4`, jitter por eixo, `max-distance≈160`                  |
| `foliage`         | Vegetação mais baixa                                 | Como `tree`, escalas maiores típicas de carpet (`scale-min≈1.4` / `scale-max≈2.8`), `max-distance≈120`                                                               |
| `creature`        | Inimigos / NPCs / actores skinned                    | `ground-align=none`, `base-y-offset=0`, upright; **sem** TerrainSpawned; escala 1; `avoid-overlaps=0`; `max-distance≈100`                                            |
| `physics-box`     | `dynamic-part` no chão                               | sem alinhamento ao declive, `ground-align=aabb`, `base-y-offset=0`, yaw aleatório, escala 1                                                                          |
| `gltf-crate`      | `GLTFDynamic`                                        | sem alinhamento ao declive, `ground-align=aabb`, `base-y-offset=0`, yaw aleatório                                                                                    |
| `place`           | Usado internamente por `place="…"` em `<GameObject>` | `align-to-terrain=1`, `ground-align=aabb`, escala 1, sem yaw aleatório, `max-slope-deg=90`                                                                           |

### `role` → perfil do grupo (`roleToProfile`)

Se o `<SpawnGroup>` / `<DynamicSpawner>` **omite** `profile` (ou usa `none`), o parser olha o `role` do **primeiro** filho com role conhecido e aplica o perfil correspondente:

| `role` no filho                  | Perfil do grupo |
| -------------------------------- | --------------- |
| `enemy`, `npc`, `creature`       | `creature`      |
| `tree`                           | `tree`          |
| `dynamic`, `pickup`, `kinematic` | `physics-box`   |
| `prop`, `static`, `visual`       | `gltf-crate`    |
| `building`                       | `none`          |

Atributos explícitos no XML **sempre** sobrescrevem o perfil. Inimigos: `role="enemy"` → `creature` (sem lift). Não usar `profile="physics-box"` em skinned.

### Filho `profile="..."` (template)

| profile         | Recipe         | Preenche se ausente                                                    |
| --------------- | -------------- | ---------------------------------------------------------------------- |
| `physics-crate` | `dynamic-part` | `shape`, `size`, `color`, `mass`, `restitution`                        |
| `gltf-crate`    | `GLTFDynamic`  | `mass`, `friction`, `collider-margin`, `collider-shape` (padrão `box`) |

## Estático vs física (templates)

O spawn instancia **qualquer recipe** declarada como filha. A distinção é a **tag** (recipe), não um modo interno do spawner:

| Objetivo                                                    | Filho típico       | Notas                                              |
| ----------------------------------------------------------- | ------------------ | -------------------------------------------------- |
| Só visual (árvores, decoração)                              | `<GLTFLoader>`     | Sem `Body`/`Collider`.                             |
| Primitiva física (caixa/cubo empurrável)                    | `<dynamic-part>`   | Malha built-in + Rapier dinâmico.                  |
| Obstáculo fixo                                              | `<static-part>`    | Corpo fixo.                                        |
| Plataforma / cinemática                                     | `<kinematic-part>` | Velocidade ou movimento scriptado.                 |
| GLB empurrável (collider no AABB: caixa, esfera ou cápsula) | `<GLTFDynamic>`    | Ver plugin `gltf-xml` / atributo `collider-shape`. |

### Atributo opcional `role`

Nos filhos do `<SpawnGroup>`: **`role="enemy" | "npc" | "creature" | "visual" | "dynamic" | …`**.

- Guarda metadado no template (ferramentas / scripts de jogo).
- Se o **grupo** não tem `profile` explícito, `role` mapeia via `roleToProfile` — `role="enemy"` → `creature` (sem fake ground).
- A recipe do filho (`GLTFLoader`, `dynamic-part`, …) continua a definir física/visual; o perfil só preenche attrs de spawn omitidos.

## Tag `<SpawnGroup>`

- **profile**: `none` | `tree` | `foliage` | `creature` | `physics-box` | `gltf-crate` — defaults do grupo (ver tabela acima).
- **Contagem** (uma das opções):
  - **count** — número fixo de instâncias (`≥ 1`).
  - **density-per-km2** — densidade na projeção horizontal **XZ** (unidades mundo = **metros**): `instâncias ≈ arredondar(densidade × área_km²)`, com `área_km² = (maxX−minX)×(maxZ−minZ) / 10⁶`. Não uses `count` ao mesmo tempo.
  - **count-min** + **count-max** — inteiro **uniforme** nesse intervalo (inclusivo) por grupo, com o mesmo **seed**; primeiro sorteio do PRNG é a contagem, depois posições/escala/yaw.
- **seed**: inteiro para PRNG (padrão `1`).
- **region-min** / **region-max**: `"x y z"`; só **x** e **z** definem a caixa no chão; **y** é ignorado.
- **align-to-terrain**: `1` alinha o eixo +Y do modelo à normal do terreno.
- **base-y-offset**: somado em Y mundo após o posicionamento no solo (default dos perfis = `0`; AABB lift já planta a sola).
- **random-yaw**: `1` aplica rotação aleatória em torno do eixo adequado (ver **yaw-distribution**).
- **scale-min** / **scale-max**: multiplicador uniforme sobre o `scale` do template (modo **linear**; intervalo contínuo).
- **scale-distribution**: `linear` (defeito) — uniforme em `[scale-min, scale-max]`; `discrete` — exige **scale-discrete** (lista de valores positivos, ex. `1.5 2 3 4`), escolha uniforme.
- **scale-discrete**: números separados por espaço; se não vazio, força escala discreta (equivalente a `scale-distribution=discrete`).
- **yaw-distribution**: `linear` (defeito) — yaw contínuo em `[0, 360°)`; `discrete` — exige **yaw-discrete-deg** e/ou **yaw-step-deg**.
- **yaw-discrete-deg**: graus permitidos (ex. `0 45 90 180`), escolha uniforme.
- **yaw-step-deg**: atalho (ex. `45`) → `0, 45, 90, …, 315°` se **yaw-discrete-deg** estiver vazio.
- **surface-epsilon**: passo em unidades mundo para a normal (padrão `0.75`).
- **max-slope-deg** (padrão `45`): inclinação máxima aceite — ângulo entre a **normal do terreno** e **+Y**. A normal é calculada a partir do **heightmap bruto** (sem o mesmo smoothing do shader), para não subestimar encostas íngremes. Se a amostra for mais íngreme, o spawner escolhe **outra posição aleatória** na mesma região e tenta de novo.
- **max-slope-attempts** (padrão `32`): tentativas por instância. Se **nenhuma** amostra cumprir o declive e `max-slope-deg` for **menor que 90°**, essa instância **não é criada** (o `count` pode ficar abaixo do pedido em regiões muito íngremes). Com `max-slope-deg` ≥ 90° aceita-se qualquer inclinação.
- **avoid-water** / **in-water** / **near-water**: `avoid-water` rejeita carve (água+barranco); `in-water` só superfície de lago (Y = waterY); `near-water` só anel de barranco/praia (pedras de margem). Não combines `in-water` com `near-water`.
- **avoid-road** (default ON em `tree`/`foliage`/`creature`/`physics-box`/`gltf-crate`): rejeita o **leito** de `<Road flatten>` (asfalto + berma + lombo) e o núcleo de `<TerrainPad>`. O talude é plantável — excluir o carve inteiro empurrava árvores para o lábio do corte (planalto alto vs relva do banco). Sob um viaduto o vão **não** conta como leito (floresta no vale), mas copas que furariam o tabuleiro são rejeitadas (`crownHitsFlyingDeck`).
- **avoid-overlaps** (padrão `1`): rejeita candidatos cujo **footprint** (disco XZ) colide com algo já registado no **registo de ocupação** — instâncias deste e de outros grupos e entidades `place` com collider. A rejeição re-amostra dentro de `max-slope-attempts`; se esgotar, a instância é omitida. Independente de ordem: cada caminho de spawn **regista e consulta**, então quem spawna depois desvia de quem veio antes (ex.: árvores instanciadas carregam async e desviam das rochas já spawnadas).
- **`<SpawnExclusion>` sempre honrado** — mesmo com `avoid-overlaps="0"` (carpet denso). Só overlaps entre props/grupos é que se desliga.
- **footprint-radius** (padrão `0` = automático): raio XZ por instância **antes da escala**. Automático = meia-largura do AABB do GLB do template (fallback `0.8` para templates `<GameObject>` sem `url`). O teste usa `raio × scale-max` (conservador); o registo usa a escala real quando conhecida.
- **`<SpawnExclusion at="16 8" radius="7">`**: disco explícito de não-spawn no registo de ocupação (água, praças, caminhos). Entidades `place` com collider registam o footprint automaticamente (raio = meia-diagonal XZ do collider, no centro com `pos-offset`).
- **pick-strategy**: `random` (padrão) ou `round-robin` entre os filhos.
- **ground-align** (perfis `tree` / `foliage`): `aabb` assenta a base do GLB; com `align-to-terrain=1` o lift segue a **normal**. `creature` usa `none`. Bounds async → catch-up em estáticos.
- **cluster-count** / **cluster-radius**: quando `cluster-count > 0`, amostras agrupam em hubs aleatórios (tufts) em vez de XZ uniforme.
- **`clusterCenters`** (API `SpawnGroupSpec`, não XML directo): hubs pré-computados `[x,z][]`. Se não vazio, `TerrainSpawnSystem` **usa estes** e **não** regenera hubs a partir de `cluster-count`. Usado pelo smart `<Vegetation>` para partilhar hubs grass→plant→flower.

## Ordem vs mutações do solo

`TerrainSpawnSystem` corre em **`setup`**, `after: [TerrainPadApplySystem, LakeApplySystem, RiverApplySystem, RoadApplySystem]`.

Antes de amostrar posições:

1. Regista todos os `<SpawnExclusion>` no occupancy.
2. Se `isTerrainHeightmapPending` **ou** `isGroundMutationPending` → defer (pad / lago / rio / `Road flatten=1` ainda não stampados). **Carve nunca faz timeout** (`placementDeferDecision`): só um heightmap que nunca chega pode cair no fallback de 600 frames. Spawn/place no sampler pré-carve = árvores a flutuar.
3. Após spawn, `registerGroundMutationCallback` + reload do heightmap → `resyncTerrainSpawnedHeights` (só entidades com `TerrainSpawned` — estáticos/`place`).

Aprendizados:

- Spawn em `simulation` sem esperar road flatten → props flutuam/enterram no anel da cidade. Gate + ordem `setup` evitam a corrida.
- Código de jogo que planta GLBs à mão (`spawnInstancedGltf` no racer) tem de esperar `isGroundReadyForPlacement` e marcar `TerrainSpawned` — senão o resync do carve não os apanha.
- Inimigos (`<Creature>`): CCT planta Y no heightfield; NavMesh só `desiredVel`. Não reintroduzir snap de sampler / visual lift nos scripts.

Filhos: um ou mais elementos com **recipe** registrada. O parser não usa o fluxo automático de filhos; grava atributos por template (incluindo `role` e **`profile`** no filho — este último só influencia defaults do template).

## Clusters e vegetação

```
clusterCount > 0, sem clusterCenters  →  spawner gera N hubs aleatórios
clusterCenters.length > 0             →  spawner usa hubs fornecidos (ignora geração)
```

Smart carpet: [`../vegetation/context.md`](../vegetation/context.md). Variação visual (hue/sat): [`../spawn-variation/context.md`](../spawn-variation/context.md).

## Pontos explícitos (`spec.points`)

```
spec.points não-vazio  →  instanceCount = points.length (sobrepõe count modes)
                        →  cada instância valida EXATAMENTE no seu ponto (1 tentativa,
                           sem re-amostra — a posição é semântica)
                        →  slope/água/estrada/occupancy falhos descartam a instância
```

Contrato para planners externos (`<NatureSpawner>`): os pontos são XZ **mundo** (sem anchor offset); variação/escala/yaw/alinhamento/instancing seguem o caminho normal. Testes: `tests/unit/spawner/points-mode.test.ts`.

## Amostragem do terreno (`surface.ts`)

- **`worldY`**: `sampleMeshSurfaceHeight` — interpola o heightmap no **lattice do mesh** (não o bilinear analítico fino). Evita props a flutuar em cristas que só existem entre vértices LOD.
- **Excepção pad / road carve**: dentro do núcleo de um `<TerrainPad>` (`padPlane`) ou do shelf esculpido de um `<Road flatten>` (`roadCarve`, leito + talude) o `worldY` é o heightfield **analítico** (`sampleHeightAt` / plano do pad). Lattice dum leaf quieto ao lado do boost da estrada lia o planalto por cortar; a câmara via o banco.
- **Resolução do lattice**: `meshSurfaceResolutionForPoint` (terrain `lod-select.ts`). Usa o **mesmo** `maxBoostOverAabb` do leaf LOD mais profundo que o chunk visual (não só `boostAt` no ponto — tile quieto ao lado de duna/pad no mesmo leaf fazia float em “algumas” árvores). Sem boost no leaf → `Terrain.resolution` (~31 m em world 2000). Com boost → lattice ~ leaf densificado (~4 m).
- **Normal para declive e rotação**: diferenças centrais no heightmap **analítico** (`sampleHeightAt`). O teste `max-slope-deg` e o alinhamento usam o relevo real, não o smoothing do shader. Duas regras finas: (1) as sondas da matriz 3×3 usam por defeito o **passo da célula** do heightfield — sondas a 1 m sobre células de ~2 m liam ravinas íngremes como rampas e deixavam tapetes `<Vegetation>` pintar paredes de corte; (2) no shelf de road carve a normal diferença a **própria superfície do carve** (não o heightfield bruto, que ali é o morro pré-corte e podia passar o gate de declive na face vertical do corte).
- **APIs**: `sampleTerrainSurface` / `sampleTerrainSurfaceMatrix` (spawner + place); `getGroundHeight` no terrain usa o mesmo contrato density-aware.

Testes: `tests/unit/spawner/mesh-surface-height.test.ts`, `tests/unit/terrain/mesh-surface-resolution.test.ts`.

## Rotação e alinhamento (`transform-merge.ts`)

Com **`align-to-terrain=1`**, a rotação final combina, **nesta ordem de aplicação ao vértice** (composição de quaternions `q_yaw * q_align * q_template`):

1. **Euler do template** (`transform` no filho) — orientação base do GLB.
2. **Alinhamento** — rotação que leva **+Y local** do modelo à **normal** do terreno no ponto de spawn.
3. **Yaw aleatório** (`random-yaw=1`) — rotação em torno da **normal** (eixo do “tronco”), não em torno de +Y mundial antes do alinhamento.

Esta ordem evita inclinar o modelo de forma errada ao misturar yaw e normal. Com **`align-to-terrain=0`**, só se aplica yaw em **+Y** mundial e o euler do template.

**Efeito visual:** em encostas, árvores/vegetação com alinhamento seguem o declive até ao limite de `max-slope-deg` (o tronco fica perpendicular à superfície nesse limite). Para troncos **sempre verticais** no mundo, use `align-to-terrain=0` no grupo (ou um perfil sem alinhamento).

## Limitações

- `getHeightAt` / amostragem usa XZ em espaço mundo como no restante do engine; terreno deslocado em XZ segue o mesmo `worldOffset` do contexto de terreno.
- One-shot: não re-spawna após hot-reload de heightmap.
- Em zonas muito íngremes, o número de instâncias efetivas pode ser **inferior** a `count` se `max-slope-deg` for restritivo e as tentativas se esgotarem.

## Extensões fora do spawner

- **`<Vegetation>`**: recipe de carpet (roles + wind + smart layers) que emite um ou mais `SpawnGroupSpec` — ver [`../vegetation/context.md`](../vegetation/context.md).
- **`<NatureSpawner>`**: planner de regras (espécies + `<Where>` + groves) que emite `SpawnGroupSpec`s com `points` explícitos — ver [`../nature/context.md`](../nature/context.md).
- **spawn-variation**: presets `tree` / `foliage` / `rock` aplicados por instância — ver [`../spawn-variation/context.md`](../spawn-variation/context.md).
- **NPCs / IA**: recipes e sistemas de jogo; o spawner só instancia o template.
- **Baked light / lightmaps**: pipeline de rendering e materiais; quando suportado, use atributos no template ou recipe dedicada.

## Exemplo — só visual (perfil `tree`)

```xml
<SpawnGroup   profile="tree"
  count="20"
  seed="7"
  region-min="-35 0 -35"
  region-max="35 0 35"
  pick-strategy="random"
>
  <GLTFLoader     role="visual"
    url="/assets/models/tree_lowpoly.glb"
    transform="scale: 1 1 1"
  ></GLTFLoader>
</SpawnGroup>
```

## Exemplo — inimigos (perfil `creature` + CCT)

```xml
<DynamicSpawner
  profile="creature"
  count="6"
  seed="101"
  region-min="-75 0 45"
  region-max="75 0 150"
>
  <Creature role="enemy" script="creature.ts">
    <GLTFLoader
      role="visual"
      url="/assets/meshes/wolf_lod0.glb"
      lod1-url="/assets/meshes/wolf_lod1.glb"
      lod2-url="/assets/meshes/wolf_lod2.glb"
    ></GLTFLoader>
  </Creature>
</DynamicSpawner>
```

Omitir `profile` e deixar só `role="enemy"` também resolve para `creature` via `roleToProfile`.

## Exemplo — caixas físicas (primitiva)

```xml
<SpawnGroup profile="physics-box" count="6" seed="3" region-min="4 0 2" region-max="10 0 6">
  <dynamic-part role="dynamic" profile="physics-crate"></dynamic-part>
</SpawnGroup>
```

## Exemplo — crates GLB

```xml
<SpawnGroup profile="gltf-crate" count="3" seed="11" region-min="-6 0 8" region-max="-2 0 12">
  <GLTFDynamic role="dynamic" profile="gltf-crate" url="/assets/models/wooden_crate.glb" collider-shape="box" transform="scale: 1 1 1"></GLTFDynamic>
</SpawnGroup>
```

Use tags de fechamento explícitas (não use self-closing em custom elements).
