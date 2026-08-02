import type { Plugin } from '../../core';
import { PrecomputeColliderSystem } from './systems';

/**
 * Consome o pré-cálculo de colisores do GameAssets (`gameassets_handoff.json`):
 * `collider="shape: precompute"` vira cápsula/cilindro baratos (raio do tronco)
 * sem fetch de `*_collision.glb`, e o navmesh corta o mesmo volume de forma
 * procedural. Sem manifest → fallback AABB-fit (comportamento pré-existente).
 */
export const PrecomputePlugin: Plugin = {
  systems: [PrecomputeColliderSystem],
};
