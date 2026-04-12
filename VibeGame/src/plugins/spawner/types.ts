import type { ParsedElement, XMLValue } from '../../core';
import type {
  ChildTemplateProfileId,
  GroundAlignMode,
  SpawnGroupProfileId,
} from './profiles';

export type SpawnTemplateRole =
  | 'visual'
  | 'dynamic'
  | 'static'
  | 'kinematic'
  | '';

export interface SpawnTemplateSpec {
  tagName: string;
  attributes: Record<string, XMLValue>;
  /** Metadado opcional (`role` no XML); não altera física nem posição. */
  role: SpawnTemplateRole;
  /** Perfil de template no filho (`profile` no XML), se houver. */
  childProfile?: ChildTemplateProfileId;
  /** When `tagName` is `entity`, XML children to attach under the spawned root. */
  entityChildren?: ParsedElement[];
}

export interface SpawnGroupSpec {
  /** Perfil do grupo (`profile` no XML), ex.: `tree`, `none`. */
  spawnGroupProfile: SpawnGroupProfileId;
  count: number;
  seed: number;
  regionMin: [number, number, number];
  regionMax: [number, number, number];
  alignToTerrain: boolean;
  baseYOffset: number;
  groundAlign: GroundAlignMode;
  randomYaw: boolean;
  scaleMin: number;
  scaleMax: number;
  surfaceEpsilon: number;
  /** Inclinação máxima (graus) entre normal do terreno e +Y; acima re-amostra posição. */
  maxSlopeDeg: number;
  /** Tentativas por instância para obter declive dentro de max-slope-deg (normal do heightmap bruto). Se esgotar e max-slope-deg for menor que 90°, essa instância não spawna. */
  maxSlopePlacementAttempts: number;
  /** round-robin | random */
  pickStrategy: 'round-robin' | 'random';
  /** Re-sample XZ when terrain would sit under a Water plane (lakes). */
  avoidWater: boolean;
  templates: SpawnTemplateSpec[];
}
