# Spawner (terreno)

Spawn procedural declarativo com `<spawn-group>` no `index.html`, alinhado à altura do terreno e opcionalmente à **inclinação** (normal por diferenças finitas no heightmap).

## Layout

- `plugin.ts` — `SpawnerPlugin` (recipe, parser, system, defaults)
- `parser.ts` — lê atributos e filhos como templates de recipe
- `systems.ts` — `TerrainSpawnSystem` (após `TransformHierarchySystem`)
- `surface.ts` — `sampleTerrainSurface`, `isNormalWithinSlopeLimit`, `normalFromHeightSampler`
- `transform-merge.ts` — parse/merge de `transform` e `composeSpawnRotation` (quaternions)
- `context.ts` — `WeakMap` State → spec por entidade
- `profiles.ts` — perfis `profile` no grupo/filhos e merge de defaults

## Perfis (`profile`)

Atributo **`profile`** no `<spawn-group>` (e opcionalmente no **filho**) preenche defaults quando o atributo correspondente **não** aparece no XML. Valores explícitos **sempre** prevalecem.

### `<spawn-group profile="...">`

| profile | Descrição | Defaults (se omitido no XML) |
|---------|------------|-------------------------------|
| `none` ou omitido | Legado | `align-to-terrain=0`, `base-y-offset=0`, `ground-align=none`, `random-yaw=0`, `scale-min/max=1`, `surface-epsilon=0.75`, `max-slope-deg=45`, `max-slope-attempts=32` |
| `tree` | Vegetação GLB | `align-to-terrain=1`, `ground-align=aabb`, `base-y-offset=0.02`, yaw aleatório, `scale-min=1.6` / `scale-max=2.2`, limites de declive como acima |
| `foliage` | Vegetação mais baixa | Como `tree`, com `scale-min=0.9` / `scale-max=1.3` |
| `physics-box` | `dynamic-part` no chão | sem alinhamento ao declive, `base-y-offset≈0.425`, yaw aleatório, escala 1 |
| `gltf-crate` | `gltf-dynamic` | sem alinhamento ao declive, `base-y-offset=0.35`, yaw aleatório, leve jitter de escala |

### Filho `profile="..."` (template)

| profile | Recipe | Preenche se ausente |
|---------|--------|----------------------|
| `physics-crate` | `dynamic-part` | `shape`, `size`, `color`, `mass`, `restitution` |
| `gltf-crate` | `gltf-dynamic` | `mass`, `friction`, `collider-margin`, `collider-shape` (padrão `box`) |

## Estático vs física (templates)

O spawn instancia **qualquer recipe** declarada como filha. A distinção é a **tag** (recipe), não um modo interno do spawner:

| Objetivo | Filho típico | Notas |
|----------|----------------|-------|
| Só visual (árvores, decoração) | `<gltf-load>` | Sem `Body`/`Collider`. |
| Primitiva física (caixa/cubo empurrável) | `<dynamic-part>` | Malha built-in + Rapier dinâmico. |
| Obstáculo fixo | `<static-part>` | Corpo fixo. |
| Plataforma / cinemática | `<kinematic-part>` | Velocidade ou movimento scriptado. |
| GLB empurrável (collider no AABB: caixa, esfera ou cápsula) | `<gltf-dynamic>` | Ver plugin `gltf-xml` / atributo `collider-shape`. |

### Atributo opcional `role` (metadado)

Nos filhos do `<spawn-group>` pode usar **`role="visual" | "dynamic" | "static" | "kinematic"`** para documentação, ferramentas ou validação futura. O valor é guardado no spec do template e **não altera** posicionamento nem física — o comportamento continua definido pela recipe (`gltf-load`, `dynamic-part`, etc.).

## Tag `<spawn-group>`

- **profile**: `none` | `tree` | `foliage` | `physics-box` | `gltf-crate` — defaults do grupo (ver tabela acima).
- **count** (obrigatório): número de instâncias.
- **seed**: inteiro para PRNG (padrão `1`).
- **region-min** / **region-max**: `"x y z"`; só **x** e **z** definem a caixa no chão; **y** é ignorado.
- **align-to-terrain**: `1` alinha o eixo +Y do modelo à normal do terreno.
- **base-y-offset**: somado em Y mundo após o posicionamento no solo (nos perfis `tree`/`foliage` costuma ser um afastamento pequeno, ex. `0.02`, depois do assentamento por AABB).
- **random-yaw**: `1` aplica rotação aleatória (eixo vertical se não alinhado; eixo normal se alinhado).
- **scale-min** / **scale-max**: multiplicador uniforme extra sobre o `scale` do template.
- **surface-epsilon**: passo em unidades mundo para a normal (padrão `0.75`).
- **max-slope-deg** (padrão `45`): inclinação máxima aceite — ângulo entre a **normal do terreno** e **+Y**. A normal é calculada a partir do **heightmap bruto** (sem o mesmo smoothing do shader), para não subestimar encostas íngremes. Se a amostra for mais íngreme, o spawner escolhe **outra posição aleatória** na mesma região e tenta de novo.
- **max-slope-attempts** (padrão `32`): tentativas por instância. Se **nenhuma** amostra cumprir o declive e `max-slope-deg` for **menor que 90°**, essa instância **não é criada** (o `count` pode ficar abaixo do pedido em regiões muito íngremes). Com `max-slope-deg` ≥ 90° aceita-se qualquer inclinação.
- **pick-strategy**: `random` (padrão) ou `round-robin` entre os filhos.
- **ground-align** (perfis `tree` / `foliage`): `aabb` usa o AABB local do GLB (`url`) para deslocar a origem e assentar o modelo no solo; com `align-to-terrain=1` o deslocamento segue a **normal**; com `0`, só **+Y** mundial.

Filhos: um ou mais elementos com **recipe** registrada. O parser não usa o fluxo automático de filhos; grava atributos por template (incluindo `role` e **`profile`** no filho — este último só influencia defaults do template).

## Amostragem do terreno (`surface.ts`)

- **`worldY`**: altura no ponto `(wx, wz)` usando o mesmo pipeline que o visual (heightmap + `heightSmoothing` / spread do terreno quando aplicável). Serve para **posicionar** o spawn na superfície que o jogador vê.
- **Normal para declive e rotação**: derivada por diferenças centrais com um sampler de altura **`heightSmoothing = 0`** (heightmap “bruto”). Assim o teste de `max-slope-deg` e a normal passada ao spawn **refletem o relevo real**, em vez de uma encosta quase plana causada pelo smoothing visual.

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

- **NPCs / IA**: recipes e sistemas de jogo; o spawner só instancia o template.
- **Baked light / lightmaps**: pipeline de rendering e materiais; quando suportado, use atributos no template ou recipe dedicada.

## Exemplo — só visual (perfil `tree`)

```xml
<spawn-group
  profile="tree"
  count="20"
  seed="7"
  region-min="-35 0 -35"
  region-max="35 0 35"
  pick-strategy="random"
>
  <gltf-load
    role="visual"
    url="/assets/models/tree_lowpoly.glb"
    transform="scale: 1 1 1"
  ></gltf-load>
</spawn-group>
```

## Exemplo — caixas físicas (primitiva)

```xml
<spawn-group profile="physics-box" count="6" seed="3" region-min="4 0 2" region-max="10 0 6">
  <dynamic-part role="dynamic" profile="physics-crate"></dynamic-part>
</spawn-group>
```

## Exemplo — crates GLB

```xml
<spawn-group profile="gltf-crate" count="3" seed="11" region-min="-6 0 8" region-max="-2 0 12">
  <gltf-dynamic role="dynamic" profile="gltf-crate" url="/assets/models/wooden_crate.glb" collider-shape="box" transform="scale: 1 1 1"></gltf-dynamic>
</spawn-group>
```

Use tags de fechamento explícitas (não use self-closing em custom elements).
