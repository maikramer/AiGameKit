# Spawner (terreno)

Spawn procedural declarativo com `<spawn-group>` no `index.html`, alinhado à altura do terreno (`TerrainLOD.getHeightAt`) e opcionalmente à inclinação (normal por diferenças finitas).

## Layout

- `plugin.ts` — `SpawnerPlugin` (recipe, parser, system, defaults)
- `parser.ts` — lê atributos e filhos como templates de recipe
- `systems.ts` — `TerrainSpawnSystem` (após `TransformHierarchySystem`)
- `surface.ts` — `sampleTerrainSurface`, `normalFromHeightSampler`
- `transform-merge.ts` — parse/merge de `transform` e rotação com quaternion
- `context.ts` — `WeakMap` State → spec por entidade
- `profiles.ts` — perfis `profile` no grupo/filhos e merge de defaults

## Perfis (`profile`)

Atributo **`profile`** no `<spawn-group>` (e opcionalmente no **filho**) preenche defaults quando o atributo correspondente **não** aparece no XML. Valores explícitos **sempre** prevalecem.

### `<spawn-group profile="...">`

| profile | Descrição | Defaults (se omitido no XML) |
|---------|------------|-------------------------------|
| `none` ou omitido | Legado | `align-to-terrain=0`, `base-y-offset=0`, `random-yaw=0`, `scale-min/max=1`, `surface-epsilon=0.75` |
| `tree` | Vegetação GLB | alinhamento ao terreno, `base-y-offset=1.5`, yaw aleatório, `scale-min=1.6` / `scale-max=2.2` |
| `foliage` | Vegetação mais baixa | igual ao tree com escalas e offset menores |
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
- **base-y-offset**: somado à altura do solo (ex.: metade da árvore).
- **random-yaw**: `1` aplica rotação aleatória (eixo vertical se não alinhado; eixo normal se alinhado).
- **scale-min** / **scale-max**: multiplicador uniforme extra sobre o `scale` do template.
- **surface-epsilon**: passo em unidades mundo para a normal (padrão `0.75`).
- **max-slope-deg** (padrão `45`): inclinação máxima aceite — ângulo entre a **normal do terreno** e **+Y**. A normal é calculada a partir do **heightmap bruto** (sem o mesmo smoothing do shader), para não subestimar encostas íngremes. Se a amostra for mais íngreme, o spawner escolhe **outra posição aleatória** na mesma região e tenta de novo.
- **max-slope-attempts** (padrão `32`): tentativas por instância. Se **nenhuma** amostra cumprir o declive e `max-slope-deg` for **menor que 90°**, essa instância **não é criada** (o `count` pode ficar abaixo do pedido em regiões muito íngremes). Com `max-slope-deg` ≥ 90° aceita-se qualquer inclinação.
- **pick-strategy**: `random` (padrão) ou `round-robin` entre os filhos.

Filhos: um ou mais elementos com **recipe** registrada. O parser não usa o fluxo automático de filhos; grava atributos por template (incluindo `role` e **`profile`** no filho — este último só influencia defaults do template).

## Limitações

- `getHeightAt` usa XZ em espaço mundo como no restante do engine; terreno deslocado em XZ pode exigir extensão futura.
- One-shot: não re-spawna após hot-reload de heightmap.

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
