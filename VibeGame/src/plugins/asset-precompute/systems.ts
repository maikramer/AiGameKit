import { defineQuery, defineSystem, type System } from '../../core';
import { Transform } from '../transforms/components';
import { Collider, ColliderShape } from '../physics/components';
import { getColliderMeshUrl } from '../physics/mesh-collider';
import { PhysicsInitializationSystem } from '../physics/systems';
import {
  getGltfLocalAABB,
  seedGltfPrecomputedBounds,
} from '../gltf-xml/gltf-bounds-cache';
import { fitColliderFromAabb } from '../gltf-xml/gltf-dynamic-collider-fit';
import {
  getPrecomputeIndexSync,
  getPrecomputeManifestState,
  loadPrecomputeManifest,
  resetPrecomputeManifestForTests,
  resolvePrecompute,
  type AssetPrecompute,
} from './manifest';

/**
 * Resolve `collider="shape: precompute"` no frame em que a entidade aparece.
 *
 * Roda no bucket `'fixed'` ANTES de `PhysicsInitializationSystem` (que cria os
 * colisores no Rapier de forma incremental): o marker `ColliderShape.Precompute`
 * nunca chega ao Rapier — ou vira cápsula/cilindro do manifest, ou cai para um
 * fallback (AABB-fit do bounds cache / cápsula default).
 *
 * Contrato de campos (ver `fitColliderFromAabb`): `radius`/`height`/`posOffsetY`
 * de cápsula/cilindro são metros **mundo** — o Rapier não os escala pelo
 * `Transform`, por isso a resolução multiplica pelos fatores de escala da
 * entidade (o spawner sorteia scale 0.9–1.4).
 */

const precomputeQuery = defineQuery([Collider]);

const DEFAULT_FALLBACK_RADIUS = 0.3;
const DEFAULT_FALLBACK_HEIGHT = 1.5;
/** Frames sem URL conhecida até cair no fallback default (XML sem mesh-url). */
const MAX_URL_WAIT_FRAMES = 120;

let manifestKicked = false;
const urlWaitFrames = new Map<number, number>();

function entityScale(eid: number): { radius: number; y: number } {
  const scaleX = Math.abs(Transform.scaleX[eid]) || 1;
  const scaleY = Math.abs(Transform.scaleY[eid]) || 1;
  const scaleZ = Math.abs(Transform.scaleZ[eid]) || 1;
  return { radius: Math.max(scaleX, scaleZ), y: scaleY };
}

function applyEntry(eid: number, entry: AssetPrecompute): void {
  const scale = entityScale(eid);
  const spec = entry.collider;
  Collider.shape[eid] =
    spec.shape === 'cylinder' ? ColliderShape.Cylinder : ColliderShape.Capsule;
  Collider.radius[eid] = Math.max(spec.radius * scale.radius, 0.01);
  Collider.height[eid] = Math.max(spec.height * scale.y, 0.01);
  Collider.posOffsetX[eid] = 0;
  Collider.posOffsetY[eid] = (spec.base_y + spec.height / 2) * scale.y;
  Collider.posOffsetZ[eid] = 0;
}

function applyFallback(eid: number, key: string): void {
  const scale = entityScale(eid);
  Collider.shape[eid] = ColliderShape.Capsule;
  Collider.posOffsetX[eid] = 0;
  Collider.posOffsetZ[eid] = 0;
  const bounds = key ? getGltfLocalAABB(key) : null;
  if (bounds) {
    const sx = (bounds.maxX - bounds.minX) * scale.radius;
    const sy = (bounds.maxY - bounds.minY) * scale.y;
    const sz = (bounds.maxZ - bounds.minZ) * scale.radius;
    const fit = fitColliderFromAabb(
      ColliderShape.Capsule,
      sx,
      sy,
      sz,
      scale.radius,
      scale.y,
      scale.radius
    );
    Collider.radius[eid] = fit.radius;
    Collider.height[eid] = fit.height;
    Collider.posOffsetY[eid] = sy / 2;
  } else {
    Collider.radius[eid] = DEFAULT_FALLBACK_RADIUS * scale.radius;
    Collider.height[eid] = DEFAULT_FALLBACK_HEIGHT * scale.y;
    Collider.posOffsetY[eid] = (DEFAULT_FALLBACK_HEIGHT / 2) * scale.y;
  }
}

export const PrecomputeColliderSystem: System = defineSystem({
  name: 'PrecomputeColliderSystem',
  group: 'fixed',
  before: [PhysicsInitializationSystem],
  update(state) {
    if (state.headless) return;
    if (!manifestKicked) {
      manifestKicked = true;
      void loadPrecomputeManifest();
    }
    const index = getPrecomputeIndexSync();
    const absent = getPrecomputeManifestState() === 'absent';
    // Manifest ainda a carregar: o marker fica e o PhysicsInitializationSystem
    // salta entidades com shape Precompute (sem collider criado no Rapier).
    if (!index && !absent) return;

    for (const eid of precomputeQuery(state.world)) {
      if (Collider.shape[eid] !== ColliderShape.Precompute) continue;
      const key = getColliderMeshUrl(state, eid) ?? '';
      if (!key) {
        const waited = (urlWaitFrames.get(eid) ?? 0) + 1;
        urlWaitFrames.set(eid, waited);
        if (waited < MAX_URL_WAIT_FRAMES) continue;
      }
      urlWaitFrames.delete(eid);

      const entry = key ? resolvePrecompute(index, key) : undefined;
      if (entry) {
        applyEntry(eid, entry);
        if (entry.aabb) seedGltfPrecomputedBounds(key, entry.aabb);
      } else {
        applyFallback(eid, key);
      }
    }
  },
});

/** Util para testes: reposição do estado global (manifest + contadores). */
export function resetPrecomputeForTests(): void {
  manifestKicked = false;
  urlWaitFrames.clear();
  resetPrecomputeManifestForTests();
}
