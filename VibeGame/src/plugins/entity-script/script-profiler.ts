/**
 * Per-script timing helpers for the EntityScript systems.
 * Aggregates many entity invocations into one custom profiler span per file.
 */

import {
  isProfilerEnabled,
  recordCustomTiming,
} from '../../core/profiler';

export type ScriptProfilePhase = 'update' | 'fixed' | 'late' | 'collision';

export interface EntityScriptFrameStat {
  /** Logical script file (e.g. `creature.ts`). */
  file: string;
  /** Profiler span name (e.g. `script/creature`). */
  span: string;
  phase: ScriptProfilePhase;
  /** Entities that invoked this script phase in the last profiled pass. */
  entities: number;
  /** Milliseconds accumulated in the last profiled pass. */
  ms: number;
}

/** Last-frame entity counts / ms by span (for the profiler panel counters). */
let lastFrameStats: EntityScriptFrameStat[] = [];
const frameStatsMap = new Map<string, EntityScriptFrameStat>();
let statsFrame = -1;

const accumMs = new Map<string, number>();
const accumEntities = new Map<string, number>();
const accumMeta = new Map<
  string,
  { file: string; phase: ScriptProfilePhase; origin: string }
>();

/** Short stable label from a script file attribute (`creature.ts` → `creature`). */
export function scriptBaseName(file: string): string {
  const base = file.trim().split('/').pop() ?? file.trim();
  return base.replace(/\.tsx?$/i, '') || 'unknown';
}

export function scriptSpanName(
  file: string,
  phase: ScriptProfilePhase = 'update'
): string {
  const base = scriptBaseName(file);
  if (phase === 'update') return `script/${base}`;
  return `script/${base}.${phase}`;
}

export function scriptOrigin(file: string): string {
  const trimmed = file.trim();
  if (!trimmed) return 'entity-script';
  if (trimmed.includes('/')) return `app/scripts/${trimmed.split('/').pop()}`;
  return `app/scripts/${trimmed}`;
}

/**
 * Start a fresh aggregation window for one EntityScript system update.
 * Resets per-frame entity counters when `frameCount` advances.
 */
export function beginScriptProfilePass(frameCount: number): boolean {
  if (!isProfilerEnabled()) return false;
  if (statsFrame !== frameCount) {
    statsFrame = frameCount;
    frameStatsMap.clear();
    lastFrameStats = [];
  }
  accumMs.clear();
  accumEntities.clear();
  accumMeta.clear();
  return true;
}

/** Accrue one entity invocation for `file` / `phase`. */
export function accumulateScriptSample(
  file: string,
  phase: ScriptProfilePhase,
  ms: number
): void {
  if (ms < 0) return;
  const span = scriptSpanName(file, phase);
  accumMs.set(span, (accumMs.get(span) ?? 0) + ms);
  accumEntities.set(span, (accumEntities.get(span) ?? 0) + 1);
  if (!accumMeta.has(span)) {
    accumMeta.set(span, {
      file: file.trim() || scriptBaseName(file),
      phase,
      origin: scriptOrigin(file),
    });
  }
}

/** Flush aggregated samples into the profiler custom spans + last-frame stats. */
export function endScriptProfilePass(): void {
  for (const [span, ms] of accumMs) {
    const meta = accumMeta.get(span)!;
    const entities = accumEntities.get(span) ?? 0;
    recordCustomTiming(span, ms, meta.origin);
    const prev = frameStatsMap.get(span);
    if (prev) {
      prev.ms += ms;
      prev.entities += entities;
    } else {
      frameStatsMap.set(span, {
        file: meta.file,
        span,
        phase: meta.phase,
        entities,
        ms,
      });
    }
  }
  lastFrameStats = Array.from(frameStatsMap.values()).sort(
    (a, b) => b.ms - a.ms
  );
  accumMs.clear();
  accumEntities.clear();
  accumMeta.clear();
}

/** Last profiled frame stats (entity counts), sorted by ms descending. */
export function getEntityScriptFrameStats(): readonly EntityScriptFrameStat[] {
  return lastFrameStats;
}

/** Time `fn` and accrue under the script span when a profile pass is active. */
export function profileScriptCall(
  profiling: boolean,
  file: string,
  phase: ScriptProfilePhase,
  fn: () => void
): void {
  if (!profiling) {
    fn();
    return;
  }
  const t0 = performance.now();
  fn();
  accumulateScriptSample(file, phase, performance.now() - t0);
}
