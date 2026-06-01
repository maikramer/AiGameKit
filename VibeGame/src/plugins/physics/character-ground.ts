import type { State } from '../../core';
import { Collider } from './components';

export const GROUND_CONTACT_SKIN = 0.05;

export function getCharacterFeetY(
  _state: State,
  entity: number,
  bodyY: number
): number {
  const halfHeight = Collider.shape[entity] === 2
    ? Collider.height[entity] / 2
    : Collider.sizeY[entity] / 2;
  const offsetY = Collider.posOffsetY[entity] || 0;
  return bodyY - halfHeight + offsetY;
}

export function getBodyYForFeetAt(
  _state: State,
  entity: number,
  feetY: number
): number {
  const halfHeight = Collider.shape[entity] === 2
    ? Collider.height[entity] / 2
    : Collider.sizeY[entity] / 2;
  const offsetY = Collider.posOffsetY[entity] || 0;
  return feetY + halfHeight - offsetY;
}
