import type { System } from '../../core';

export const SpriteSystem: System = {
  group: 'draw',
  // No-op: sprite rendering is handled by the rendering plugin
  // which reads Sprite component data when present.
};
