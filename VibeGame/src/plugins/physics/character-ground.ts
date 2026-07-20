import type { State } from '../../core';
import { Collider, ColliderShape } from './components';

export const GROUND_CONTACT_SKIN = 0.05;

/**
 * Half-extent from collider center to its lowest point along local Y.
 * Rapier capsules use `capsule(height/2, radius)` — total span is
 * `height + 2*radius`, so the bottom is `height/2 + radius` below center.
 */
function colliderHalfExtentY(entity: number): number {
  if (Collider.shape[entity] === ColliderShape.Capsule) {
    return Collider.height[entity] / 2 + Collider.radius[entity];
  }
  return Collider.sizeY[entity] / 2;
}

export function getCharacterFeetY(
  _state: State,
  entity: number,
  bodyY: number
): number {
  const half = colliderHalfExtentY(entity);
  const offsetY = Collider.posOffsetY[entity] || 0;
  return bodyY - half + offsetY;
}

export function getBodyYForFeetAt(
  _state: State,
  entity: number,
  feetY: number
): number {
  const half = colliderHalfExtentY(entity);
  const offsetY = Collider.posOffsetY[entity] || 0;
  return feetY + half - offsetY;
}
