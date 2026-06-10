# Plugin `raycast`

Raycasts por entidade com `RaycastSource` + `RaycastHit`.

- `mode = 0` (default): mundo Rapier (`castRayAndGetNormal`). Só considera
  colliders cuja `Collider.membershipGroups` sobrepõe `layerMask` (mesma
  semântica do modo BVH). Colliders do motor sem entidade ECS (e.g.
  heightfields do terreno) são sempre atingíveis e devolvem
  `hitEntity === NULL_ENTITY` com o hit válido.
- `mode = 1`: índice BVH (`castBvhRay`) sobre malhas estáticas; `layerMask`
  filtra por sobreposição com `BvhEntry.layer`.

**In-scope:** picking físico, linha de visão, interação por raio.
