import type * as THREE from 'three';

const patchedMats = new WeakSet<THREE.Material>();

/**
 * InstancedMesh2 sets `USE_INSTANCING_INDIRECT` and injects `uVar*` locals at
 * the start of `main` *after* this onBeforeCompile runs — but defines are
 * applied at compile time, so the ifdef still gates correctly. Materials that
 * compile outside InstancedMesh2 (shared GLB mats, probes) skip the block and
 * never see undeclared identifiers.
 */
const FRAGMENT_MAIN = /* glsl */ `
#ifdef USE_INSTANCING_INDIRECT
  // After map_* so contrast/brightness hit textured albedo (not pre-map white).
  diffuseColor.rgb = (diffuseColor.rgb - 0.5) * uVarContrast + 0.5;
  diffuseColor.rgb *= uVarBrightness;
#endif
`;

function injectVariationFragment(fragmentShader: string): string {
  if (fragmentShader.includes('uVarContrast + 0.5')) return fragmentShader;
  if (fragmentShader.includes('#include <map_fragment>')) {
    return fragmentShader.replace(
      '#include <map_fragment>',
      `#include <map_fragment>\n${FRAGMENT_MAIN}`
    );
  }
  if (fragmentShader.includes('#include <color_fragment>')) {
    return fragmentShader.replace(
      '#include <color_fragment>',
      `#include <color_fragment>\n${FRAGMENT_MAIN}`
    );
  }
  // Last resort: before lighting output.
  if (fragmentShader.includes('#include <opaque_fragment>')) {
    return fragmentShader.replace(
      '#include <opaque_fragment>',
      `${FRAGMENT_MAIN}\n#include <opaque_fragment>`
    );
  }
  return fragmentShader;
}

/**
 * Patch a material so fragment colour applies per-instance brightness/contrast.
 * Must run BEFORE the material is handed to InstancedMesh2 (same rule as wind).
 * Call `initUniformsPerInstance(INSTANCE_VARIATION_UNIFORM_SCHEMA)` on the mesh
 * so `uVarBrightness` / `uVarContrast` exist as locals inside `main`.
 */
export function maybePatchInstanceVariationMaterial(
  mat: THREE.Material | null | undefined
): void {
  if (!mat) return;
  if (patchedMats.has(mat)) return;
  // CustomShaderMaterial owns onBeforeCompile — skip.
  if ('__csm' in mat) return;

  const prev = mat.onBeforeCompile;
  mat.onBeforeCompile = (shader, renderer) => {
    if (typeof prev === 'function') prev.call(mat, shader, renderer);
    shader.fragmentShader = injectVariationFragment(shader.fragmentShader);
  };
  const prevKey = mat.customProgramCacheKey?.bind(mat);
  mat.customProgramCacheKey = () =>
    `${prevKey ? prevKey() : ''}|spawnVarBC4`;
  mat.needsUpdate = true;
  patchedMats.add(mat);
}

/** Schema for InstancedMesh2.initUniformsPerInstance. */
export const INSTANCE_VARIATION_UNIFORM_SCHEMA = {
  fragment: {
    uVarBrightness: 'float' as const,
    uVarContrast: 'float' as const,
  },
};
