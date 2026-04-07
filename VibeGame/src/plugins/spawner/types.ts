import type { XMLValue } from '../../core';
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
  /** Tentativas por instância antes de usar a última amostra (mesmo íngreme). */
  maxSlopePlacementAttempts: number;
  /** round-robin | random */
  pickStrategy: 'round-robin' | 'random';
  templates: SpawnTemplateSpec[];
}
