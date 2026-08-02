# asset-precompute — colisores pré-calculados

Consome o `gameassets_handoff.json` (emitido pelo `gameassets handoff`, com o
bloco `precompute` por asset) para montar colisores primitivos baratos e
acelerar o boot:

- `collider="shape: precompute; mesh-url: <url do GLB visual>"` → cápsula
  (árvore, raio do tronco) ou cilindro (pedra) — **sem fetch de
  `*_collision.glb`**.
- `seedGltfPrecomputedBounds` → `ground-align="aabb"` / lift do spawner usam o
  AABB pré-calculado (sem `Box3.setFromObject`).
- Navmesh: cápsulas/cilindros fixos cortam o bake de forma **procedural**
  (prisma de 8 lados em `navmesh/geometry.ts`) — o bake não espera downloads.

## Contrato de campos

`radius` / `height` / `posOffsetY` de cápsula/cilindro são **metros mundo** —
o Rapier não os escala pelo `Transform` (ver `fitColliderFromAabb`). O sistema
de resolução multiplica pelos fatores de escala da entidade (spawner sorteia
scale 0.9–1.4).

## Fluxo

1. `PrecomputeColliderSystem` (bucket `fixed`, antes de
   `PhysicsInitializationSystem`) dispara o fetch do manifest uma vez.
2. Entidades com shape `Precompute` são resolvidas no frame em que aparecem:
   manifest hit → campos do colisor; miss → fallback AABB-fit do bounds cache;
   sem bounds → cápsula default.
3. O marker `Precompute` **nunca chega ao Rapier**: enquanto o manifest está
   `loading`, `PhysicsInitializationSystem` salta essas entidades.

## Fallbacks

- Manifest ausente (404 / release sem `gameassets_handoff.json`): AABB-fit —
  comportamento pré-existente, sem regressão.
- XML sem `mesh-url` no colisor: espera 120 frames pela URL, depois cápsula
  default.
- `collider="shape: cylinder"` explícito também funciona (sem precompute).
