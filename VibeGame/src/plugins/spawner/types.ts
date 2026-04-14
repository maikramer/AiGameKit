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

/** `fixed` = `count`; `density` = `density-per-km2` × área XZ (unidades mundo = m); `random-range` = inteiro uniforme em `[count-min, count-max]`. */
export type SpawnCountMode = 'fixed' | 'density' | 'random-range';

/** Escala uniforme no intervalo ou escolha entre valores listados. */
export type ScaleDistributionMode = 'linear' | 'discrete';

/** Rotação (yaw em torno da normal do terreno se `align-to-terrain`, senão Y) contínua ou ângulos listados. */
export type YawDistributionMode = 'linear' | 'discrete';

export interface SpawnGroupSpec {
  /** Perfil do grupo (`profile` no XML), ex.: `tree`, `none`. */
  spawnGroupProfile: SpawnGroupProfileId;
  /** Modo de contagem de instâncias (ver `count`, `density-per-km2`, `count-min` / `count-max`). */
  spawnCountMode: SpawnCountMode;
  /** Instâncias quando `spawn-count-mode` implícito fixo (atributo `count`). */
  count: number;
  /** Objetos por km² na projeção XZ (região × largura×profundidade em m² ÷ 10⁶). Só com `spawnCountMode=density`. */
  densityPerKm2: number;
  /** Inclusive; só com `spawnCountMode=random-range`. */
  countRangeMin: number;
  /** Inclusive; só com `spawnCountMode=random-range`. */
  countRangeMax: number;
  seed: number;
  regionMin: [number, number, number];
  regionMax: [number, number, number];
  alignToTerrain: boolean;
  baseYOffset: number;
  groundAlign: GroundAlignMode;
  randomYaw: boolean;
  scaleDistribution: ScaleDistributionMode;
  /** Ângulos em graus; se não vazio e `scale-distribution=discrete`, escolha uniforme. */
  scaleDiscreteValues: number[];
  scaleMin: number;
  scaleMax: number;
  yawDistribution: YawDistributionMode;
  /** Yaw extra em graus (0–360); vazio = linear em `[0, 360)` se `yaw-distribution=linear`. */
  yawDiscreteDeg: number[];
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
