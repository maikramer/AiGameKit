# Precompute de colisores (cápsula/cilindro) — GameAssets → VibeGame

Data: 2026-08-01 · Status: live (master pipeline Round 3 + `PrecomputePlugin`)

## Problema

Árvores e pedras coletáveis usavam `collider="shape: trimesh; mesh-url:
…_collision.glb"`: cada instância baixava + parseava o GLB de colisão e o
bake do NavMesh **esperava** esses downloads; por isso os spawners
harvestable ficavam na ordem das dezenas (ver o antigo comentário de custo em
`forest.xml`). Com centenas de coletáveis o custo explodia.

## Solução

1. **`aigamekit-lab precompute`** (parser header-only, sem bpy — reusa
   `glb_extract_meta` + decode de POSITION float32/quantizado) decide o
   colisor primitivo ideal por asset e emite `{id}_precompute.json`:
   - árvore split (existe `{id}_stump_collision.glb`) → **cápsula do AABB do
     stump** — o tronco exato. `source: "stump"` (a largura NUNCA vem da copa).
   - `category: vegetation` sem stump → **fatia inferior** (`y < y_min +
     0.35·H`): raio só do tronco. `source: "trunk-slice"`.
   - `category: rock|terrain` → **cilindro** do AABB completo (topo plano,
     mais fiel a um penedo). `source: "aabb"`.
   - resto → cápsula do AABB. `source: "aabb"`.
   - **Raio por AABB = `max(dx, dz)/2`, SEM cap pela altura** — círculo que
     contém o footprint XZ (envelope do trimesh antigo). O cap `dy/2`
     (herdado do `fitColliderFromAabb`) fazia stumps largos e baixos
     (alargamento de raízes: carvalho 1.86×0.6 m) ficarem com raio 0.3 e o
     jogador entrava no tronco.
   - Saída também carrega `aabb` (espaço mundo do GLB) e
     `collectible_hint.kind` (vegetation→wood, rock/terrain→stone).

2. **GameAssets** chama o precompute no fim do master pipeline
   (`_post_text3d_mesh_extras`, cobre batch/dash/resume) e o **handoff** faz
   inline do bloco nas rows do `gameassets_handoff.json` (1 fetch só na
   engine). Sem `aigamekit-lab` no PATH ou falha → skip soft, sem falhar o
   batch; o handoff omite o bloco e a engine degrada.

3. **Engine (`PrecomputePlugin`)**:
   - `collider="shape: precompute; mesh-url: <url do GLB visual>"` →
     `PrecomputeColliderSystem` (bucket `fixed`, `before:
     PhysicsInitializationSystem`) resolve do manifest para cápsula/cilindro.
   - **Sem fetch de `*_collision.glb`** — o mesh-collider só busca para
     TriMesh/ConvexHull.
   - `seedGltfPrecomputedBounds` → `ground-align="aabb"` / lift do spawner
     usam o AABB pré-calculado (sem `Box3.setFromObject`).
   - NavMesh: `isFixedObstacle` aceita Capsule/Cylinder; o carve é um
     **prisma de 8 lados** procedural (`appendPrism`) — o bake não espera
     downloads (`prefetchNavmeshObstacles`/`navmeshObstaclesLoaded` só
     rastreiam trimesh/hull).

## Gotchas (lições)

- **Campos de cápsula/cilindro são metros MUNDO** — o Rapier NÃO os escala
  pelo `Transform` (contrato em `fitColliderFromAabb`). A resolução
  multiplica `radius × max(scaleX, scaleZ)` e `height × scaleY` (o spawner
  sorteia scale 0.9–1.4).
- **O marker `Precompute` nunca chega ao Rapier**: enquanto o manifest está
  `loading`, `PhysicsInitializationSystem` salta essas entidades (senão
  criava um cuboid default). Manifest ausente (404) → fallback AABB-fit do
  bounds cache; sem bounds → cápsula default (0.3, 1.5).
- **Prisma base unit tem y ∈ [-0.5, 0.5]** — o fator Y é `2×halfH` (não
  `halfH`); metade da altura escondia o bug (minY = 0.3 com offset 0.6).
- **Prisma sem trim `OBSTACLE_NAV_HEIGHT`** (`fullCarve`): parede vertical
  não cria rampa — consistente com `appendBox`. O trim de 2.5 m existe para
  hulls de tronco→copa cujo topo alargado virava rampa caminhável.
- **Árvores `break-style: fall` usam o stump como colisor** (regra da
  engine): a cápsula do stump (0.6 m ≈ cut-height 0.7) preserva o envelope
  atual — sem parede invisível depois da queda.
- **Queda de árvores instanciadas (`startTreeFall`)**: spawners estáticos
  renderizam tudo via `InstancedMesh2` (sem `GltfRootGroup` por entidade) —
  a queda caía no burst plain. Fix: quando `findVisualGroup` é undefined, o
  fallback clona o **master GLB (cache do loader)** na pose da instância
  (Transform capturado antes do destroy) e corre o mesmo `pushTreeFallFx`.
  `startRockShatter` não precisa do grupo (só amostra a cor).
- **Stump persistente pós-queda (`spawnPersistentStump`)**: a entidade era
  destruída no break (colisor ia junto) e o stump do FX desvanecia — o
  jogador atravessava a base. Agora a queda cria uma entidade estática nova
  com o **colisor cápsula copiado da árvore em pé** (o precompute das split
  É o stump) + o visual da peça `Stump` (clone local, `scene.add` +
  `setupCsmMaterials` + `GltfPending.loaded` para o `GltfSceneSyncSystem`
  aplicar o Transform). Armadilha: o grupo registado não entra na cena
  sozinho — sem `scene.add` o stump fica invisível (colisor ativo, mesh sem
  render).
- **Fallback do release pin**: o simple-rpg baixa os GLBs de uma release; o
  `gameassets_handoff.json` foi gerado por backfill (`aigamekit-lab
  precompute` sobre `public/assets/meshes/*` + categorias do `game.yaml`) e
  passa a fazer parte dos assets de release. XML sem manifest → cápsula da
  copa (fallback AABB-fit) — regressão de bloqueio, evitar.

## Schema do bloco `precompute` (row do handoff)

```json
{
  "version": 1,
  "asset_id": "pine_dark",
  "category": "vegetation",
  "aabb": { "min": [x, y, z], "max": [x, y, z] },
  "collider": { "shape": "capsule|cylinder", "radius": 0.23, "height": 0.6, "base_y": 0.0 },
  "source": "stump|trunk-slice|aabb",
  "collectible_hint": { "kind": "wood|stone|null" }
}
```

## Ficheiros-chave

- `AiGameKitLab/src/aigamekit_lab/precompute.py` (+ `glb_meta.glb_extract_meta`
  devolve agora `world_bounds_min/max`); CLI `aigamekit-lab precompute`.
- `GameAssets/src/gameassets/pipeline.py` (`_emit_precompute`),
  `paths.py` (`_precompute_path`), `handoff_export.py` (inline na row).
- `VibeGame/src/plugins/asset-precompute/` (manifest + sistema),
  `physics/` (shape `Cylinder`/`Precompute`), `navmesh/geometry.ts`
  (`appendPrism`), `gltf-xml/gltf-bounds-cache.ts` (`seedGltfPrecomputedBounds`).
- Exemplo: `VibeGame/examples/simple-rpg/public/world/**` (spawners com
  `collider="shape: precompute"`) + `public/assets/gameassets_handoff.json`.
