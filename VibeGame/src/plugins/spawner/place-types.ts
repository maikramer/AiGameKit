import type { GroupSpawnDefaults } from './profiles';
import type { SpawnTemplateSpec } from './types';

/**
 * Deterministic placement at fixed XZ with terrain height / alignment resolved at runtime.
 */
export interface PlacementSpec {
  /** World-space offset X from the parent entity anchor (same convention as spawn-group region). */
  atX: number;
  /** World-space offset Z from the parent entity anchor. */
  atZ: number;
  /** Resolved spawn fields (align, ground-align, y offset, slope, epsilon, scale). */
  spawn: GroupSpawnDefaults;
  templates: SpawnTemplateSpec[];
}
