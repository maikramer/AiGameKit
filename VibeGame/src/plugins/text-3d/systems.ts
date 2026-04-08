import * as THREE from 'three';
import { defineQuery, type System } from '../../core';
import { loadGltfToScene } from '../../extras/gltf-bridge';
import { getScene } from '../rendering';
import { Text3dModel } from './components';

// Contexto singleton para mapear entity → URL
const text3dUrls = new Map<number, string>();
const text3dInFlight = new Map<number, boolean>();
const text3dGroups = new Map<number, THREE.Group>();

/** Associa uma URL de modelo Text3D a uma entidade. */
export function setText3dUrl(eid: number, url: string): void {
  text3dUrls.set(eid, url);
}

/** Retorna a URL associada à entidade. */
export function getText3dUrl(eid: number): string | undefined {
  return text3dUrls.get(eid);
}

const text3dQuery = defineQuery([Text3dModel]);

export const Text3dLoadSystem: System = {
  group: 'setup',
  update: (state) => {
    const scene = getScene(state);
    if (!scene) return;

    for (const eid of text3dQuery(state.world)) {
      if (Text3dModel.pending[eid] === 0) continue;
      if (text3dInFlight.get(eid)) continue;

      const url = text3dUrls.get(eid);
      if (!url) {
        Text3dModel.pending[eid] = 0;
        continue;
      }

      text3dInFlight.set(eid, true);

      void loadGltfToScene(state, url)
        .then((group) => {
          // Aplica escala do Text3D (se definida)
          const s = Text3dModel.scale[eid];
          if (s > 0 && s !== 1) {
            group.scale.set(s, s, s);
          }

          // Aplica tint se definido
          const tint = Text3dModel.tint[eid];
          if (tint !== 0) {
            group.traverse((child) => {
              if ((child as THREE.Mesh).isMesh) {
                const mat = (child as THREE.Mesh)
                  .material as THREE.MeshStandardMaterial;
                if (Array.isArray(mat)) {
                  mat.forEach((m) => {
                    if ('color' in m)
                      (m as THREE.MeshStandardMaterial).color?.setHex(tint);
                  });
                } else if ('color' in mat) {
                  mat.color?.setHex(tint);
                }
              }
            });
          }

          text3dGroups.set(eid, group);
        })
        .catch((err: unknown) => {
          console.error('[text-3d] Falha ao carregar modelo Text3D:', err);
        })
        .finally(() => {
          Text3dModel.pending[eid] = 0;
          text3dInFlight.set(eid, false);
        });
    }
  },
};

export const Text3dCleanupSystem: System = {
  group: 'draw',
  update: (state) => {
    const scene = getScene(state);
    if (!scene) return;

    for (const [eid, group] of text3dGroups) {
      if (!state.exists(eid) || !state.hasComponent(eid, Text3dModel)) {
        scene.remove(group);
        text3dGroups.delete(eid);
        text3dUrls.delete(eid);
        text3dInFlight.delete(eid);
      }
    }
  },
};
