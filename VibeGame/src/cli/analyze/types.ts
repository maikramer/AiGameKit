export type AnalyzeSeverity = 'error' | 'warn' | 'info';

export type AnalyzeCode =
  'include' | 'parse' | 'asset' | 'overlap' | 'bounds' | 'road';

export interface AnalyzeIssue {
  severity: AnalyzeSeverity;
  code: AnalyzeCode;
  message: string;
  /** Extra lines (A/B labels, overlap area, …). */
  detail?: string[];
}

/** Axis-aligned footprint on XZ (world metres). */
export interface Footprint {
  id: string;
  label: string;
  minX: number;
  maxX: number;
  minZ: number;
  maxZ: number;
  kind: 'composition' | 'gameobject' | 'other';
  /**
   * Same-Composition solids share a groupId so corner-touching wall
   * segments do not report as overlaps with each other.
   */
  groupId?: string;
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
}
