/* global fetch */
import * as THREE from 'three';

import { createGLTFLoader } from '../../extras/gltf-bridge';

/** Chave = URL normalizada (trim); valores em espaço local do root do GLB (Y up). */
const boundsByUrl = new Map<string, { minY: number; maxY: number }>();

const warnedMissing = new Set<string>();
const prefetchInflight = new Set<string>();

export function normalizeGltfUrlKey(url: string): string {
  return url.trim();
}

/**
 * Regista o intervalo Y do AABB do modelo (ex.: após `loadGltfToScene`, antes de aplicar transform da entidade).
 * Usado pelo spawn com `ground-align="aabb"` para levantar a origem até o solo (`-minY * escala` ao longo da normal).
 */
export function registerGltfLocalYBounds(
  url: string,
  root: THREE.Object3D
): void {
  const key = normalizeGltfUrlKey(url);
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;
  boundsByUrl.set(key, { minY: box.min.y, maxY: box.max.y });
}

export function getGltfLocalYBounds(
  url: string
): { minY: number; maxY: number } | null {
  return boundsByUrl.get(normalizeGltfUrlKey(url)) ?? null;
}

export function isGltfBoundsPrefetchInflight(url: string): boolean {
  const key = normalizeGltfUrlKey(url);
  return prefetchInflight.has(key);
}

export function warnMissingGltfBoundsOnce(url: string): void {
  const key = normalizeGltfUrlKey(url);
  if (warnedMissing.has(key)) return;
  warnedMissing.add(key);
  console.warn(
    `[spawn-group] AABB ainda não disponível para "${key}". ` +
      `Coloque um <gltf-load url="..."> antes do spawn ou aguarde o carregamento; ` +
      `até lá usa-se só base-y-offset.`
  );
}

/**
 * Carrega o GLB em segundo plano (sem adicionar à cena), só para registar minY/maxY.
 * Chamado ao fazer parse de `<spawn-group>` para reduzir corridas com o primeiro spawn.
 */
export function prefetchGltfLocalYBounds(url: string): void {
  const key = normalizeGltfUrlKey(url);
  if (!key || boundsByUrl.has(key) || prefetchInflight.has(key)) return;
  if (typeof fetch !== 'function') return;

  prefetchInflight.add(key);
  const loader = createGLTFLoader();
  fetch(key)
    .then((r) => {
      if (!r.ok) throw new Error(String(r.status));
      return r.arrayBuffer();
    })
    .then((buf) => loader.parseAsync(buf, key))
    .then((gltf) => {
      registerGltfLocalYBounds(key, gltf.scene);
      prefetchInflight.delete(key);
    })
    .catch(() => {
      prefetchInflight.delete(key);
    });
}
