export type AnalyzeSeverity = 'error' | 'warn' | 'info';

export type AnalyzeCode =
  | 'include'
  | 'parse'
  | 'asset'
  | 'overlap'
  | 'bounds'
  | 'road'
  | 'recipe'
  | 'attr'
  | 'script'
  | 'spawner'
  | 'world'
  | 'name';

export type AnalyzePluginSet = 'default' | 'rpg' | 'all';

export interface AnalyzeIssue {
  severity: AnalyzeSeverity;
  code: AnalyzeCode;
  message: string;
  /** Extra lines (A/B labels, overlap area, …). */
  detail?: string[];
}

/** Axis-aligned footprint on XZ (world metres), with optional Y range. */
export interface Footprint {
  id: string;
  label: string;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  /** Inclusive Y range; defaults 0..0 when unknown. */
  minY: number;
  maxY: number;
  kind: 'composition' | 'gameobject' | 'other' | 'pad' | 'road';
  /**
   * Same-Composition solids share a groupId so corner-touching wall
   * segments do not report as overlaps with each other.
   */
  groupId?: string;
  /**
   * Max solid↔solid penetration depth (m) tolerated on XZ (`min(Δx,Δz)`).
   * From XML `overlap-max`; default 0 (none). Pair uses `max` of both.
   */
  overlapMax?: number;
}

export interface AnalyzeResult {
  entry: string;
  publicDir: string;
  footprintCount: number;
  issues: AnalyzeIssue[];
  errorCount: number;
  warnCount: number;
}

export interface AnalyzeOptions {
  entry: string;
  publicDir: string;
  /** Fail exit on warn as well as error. Default: error only. */
  failOn?: 'error' | 'warn';
  /** Dir of entity scripts (basename match). Auto-detect if omitted. */
  scriptsDir?: string | null;
  /**
   * Recipe registry for unknown-tag checks.
   * `all` (default) = DefaultPlugins + RpgPlugins.
   */
  plugins?: AnalyzePluginSet;
}
