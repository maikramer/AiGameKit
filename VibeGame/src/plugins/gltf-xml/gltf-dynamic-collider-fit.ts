import { ColliderShape } from '../physics/components';

/** Alinhado a `MIN_HALF_DIM` em `gltf-dynamic-system` (AABB mínimo por eixo). */
export const GLTF_DYNAMIC_MIN_HALF_DIM = 0.05;

export interface GltfDynamicColliderFit {
  shape: (typeof ColliderShape)[keyof typeof ColliderShape];
  sizeX: number;
  sizeY: number;
  sizeZ: number;
  radius: number;
  height: number;
}

/**
 * Calcula `Collider` a partir do tamanho AABB do modelo (já com margem), em **espaço
 * mundo** (`Box3.setFromObject` após `updateMatrixWorld`), e escalas do `Transform`.
 *
 * **Contrato de entrada:** `sx`/`sy`/`sz` são dimensões mundo do AABB (incluindo
 * escala do `Transform` no mesh). `tsx`/`tsy`/`tsz` são os fatores de escala ECS.
 *
 * - **Box**: cuboide alinhado aos eixos; `size*` = dimensão local (`mundo / escala`
 *   por eixo) porque `createColliderForEntity` re-multiplica `size*` pela escala.
 * - **Sphere**: esfera mínima que contém o AABB; raio = metade da diagonal;
 *   `sizeX` = diâmetro local = `2R / scaleX`.
 * - **Capsule**: eixo Y; `radius` e `height` (segmento central) em **metros mundo**
 *   derivados diretamente de `sx`/`sy`/`sz` — o pipeline de física **não** multiplica
 *   estes campos pela escala do `Transform` (ver `createColliderDescriptor`). Com
 *   escala ≠ 1, passar AABB local em vez de mundo subdimensiona o colisor; use sempre
 *   dimensões de `setFromObject`.
 */
export function fitColliderFromAabb(
  colliderShape: number,
  sx: number,
  sy: number,
  sz: number,
  tsx: number,
  tsy: number,
  tsz: number
): GltfDynamicColliderFit {
  const tsxSafe = Math.max(Math.abs(tsx), 1e-6);
  const tsySafe = Math.max(Math.abs(tsy), 1e-6);
  const tszSafe = Math.max(Math.abs(tsz), 1e-6);

  if (colliderShape === ColliderShape.Sphere) {
    const R = 0.5 * Math.sqrt(sx * sx + sy * sy + sz * sz);
    return {
      shape: ColliderShape.Sphere,
      sizeX: (2 * R) / tsxSafe,
      sizeY: 0,
      sizeZ: 0,
      radius: 0.5,
      height: 1,
    };
  }

  if (colliderShape === ColliderShape.Capsule) {
    let r = Math.min(sx, sz) / 2;
    r = Math.min(r, sy / 2);
    r = Math.max(r, GLTF_DYNAMIC_MIN_HALF_DIM);
    const segment = Math.max(0, sy - 2 * r);
    return {
      shape: ColliderShape.Capsule,
      sizeX: 1,
      sizeY: 1,
      sizeZ: 1,
      radius: r,
      height: segment,
    };
  }

  return {
    shape: ColliderShape.Box,
    sizeX: sx / tsxSafe,
    sizeY: sy / tsySafe,
    sizeZ: sz / tszSafe,
    radius: 0.5,
    height: 1,
  };
}
