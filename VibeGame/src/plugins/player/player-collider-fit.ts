import { ColliderShape } from '../physics/components';
import {
  fitColliderFromAabb,
  GLTF_DYNAMIC_MIN_HALF_DIM,
} from '../gltf-xml/gltf-dynamic-collider-fit';

export interface PlayerColliderAabb {
  min: { x: number; y: number; z: number };
  max: { x: number; y: number; z: number };
}

export interface PlayerColliderFitInput {
  box: PlayerColliderAabb;
  yOffset: number;
  margin?: number;
  scaleX?: number;
  scaleY?: number;
  scaleZ?: number;
}

export interface PlayerColliderFitResult {
  shape: (typeof ColliderShape)[keyof typeof ColliderShape];
  radius: number;
  height: number;
  sizeX: number;
  sizeY: number;
  sizeZ: number;
  posOffsetX: number;
  posOffsetY: number;
  posOffsetZ: number;
}

/**
 * Ajusta cápsula do jogador ao AABB do GLB: pés na origem da entidade (`yOffset`),
 * centro do colisor alinhado ao centro do AABB em espaço local.
 */
export function applyPlayerColliderFromAabb(
  input: PlayerColliderFitInput
): PlayerColliderFitResult {
  const margin = input.margin ?? 0.02;
  const tsx = Math.max(Math.abs(input.scaleX ?? 1), 1e-6);
  const tsy = Math.max(Math.abs(input.scaleY ?? 1), 1e-6);
  const tsz = Math.max(Math.abs(input.scaleZ ?? 1), 1e-6);

  let sx = input.box.max.x - input.box.min.x + 2 * margin;
  let sy = input.box.max.y - input.box.min.y + 2 * margin;
  let sz = input.box.max.z - input.box.min.z + 2 * margin;
  const minDim = GLTF_DYNAMIC_MIN_HALF_DIM * 2;
  sx = Math.max(sx, minDim);
  sy = Math.max(sy, minDim);
  sz = Math.max(sz, minDim);

  const fit = fitColliderFromAabb(
    ColliderShape.Capsule,
    sx,
    sy,
    sz,
    tsx,
    tsy,
    tsz
  );

  const centerX = (input.box.min.x + input.box.max.x) / 2;
  const centerY = (input.box.min.y + input.box.max.y) / 2;
  const centerZ = (input.box.min.z + input.box.max.z) / 2;

  return {
    shape: fit.shape,
    radius: fit.radius,
    height: fit.height,
    sizeX: fit.sizeX,
    sizeY: fit.sizeY,
    sizeZ: fit.sizeZ,
    posOffsetX: centerX,
    posOffsetY: centerY + input.yOffset,
    posOffsetZ: centerZ,
  };
}
