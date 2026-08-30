import {
  getAllEntities,
  getTotalActiveCoroutineCount,
  isPhysicsHeld,
  getLoadingProgress,
} from '../../core';
import type { State } from '../../core';
import type { LogLevel } from '../../core/utils/logger';
import { getActiveGltfLoadCount } from '../../extras/gltf-bridge';
import { getDebugRegistry } from './registry';
import {
  aggregateDiagnostics,
  getDiagnostics,
  getResourceTimings,
  type DiagnosticEntry,
  type ResourceTimingEntry,
} from './diagnostics';

/**
 * One-call, bounded, JSON-only dump of everything an AI agent needs to debug a
 * running game through a browser MCP (`__VIBEGAME__.report()`). Every section
 * has a size cap so a single `browser_evaluate` result stays within tool
 * limits; the diagnostics buffer remains the lossless pollable source via
 * `logs({ since })`.
 */

type TypedArrayField =
  | Float32Array
  | Float64Array
  | Int32Array
  | Uint8Array
  | Uint16Array
  | Uint32Array;

function isTypedArrayField(v: unknown): v is TypedArrayField {
  return (
    v instanceof Float32Array ||
    v instanceof Float64Array ||
    v instanceof Int32Array ||
    v instanceof Uint8Array ||
    v instanceof Uint16Array ||
    v instanceof Uint32Array
  );
}

export function extractComponentFields(
  state: State,
  eid: number,
  compName: string
): Record<string, number> | null {
  const comp = state.getComponent(compName);
  if (!comp || !state.hasComponent(eid, comp)) return null;
  const fields: Record<string, number> = {};
  for (const key in comp) {
    if (key.startsWith('_')) continue;
    const field = (comp as Record<string, unknown>)[key];
    if (isTypedArrayField(field)) {
      fields[key] = field[eid];
    }
  }
  return fields;
}

export function extractAllComponents(
  state: State,
  eid: number
): Record<string, Record<string, number>> {
  const result: Record<string, Record<string, number>> = {};
  for (const compName of state.getComponentNames()) {
    const fields = extractComponentFields(state, eid, compName);
    if (fields) result[compName] = fields;
  }
  return result;
}

export function truncate(value: string, max: number): string {
  return value.length > max ? value.slice(0, max - 3) + '...' : value;
}

export function safeStringify(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function formatDebugValue(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') return truncate(value, 40);
  if (
    typeof value === 'number' ||
    typeof value === 'boolean' ||
    typeof value === 'bigint'
  ) {
    return String(value);
  }
  if (typeof value === 'function') return 'ƒ';
  if (isTypedArrayField(value)) {
    return `${value.constructor.name}(${value.length})`;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return '[]';
    return value.length > 8
      ? `Array(${value.length})`
      : truncate(safeStringify(value), 40);
  }
  const ctor = (value as { constructor?: { name?: string } }).constructor;
  return ctor && ctor.name && ctor.name !== 'Object' ? ctor.name : '{obj}';
}

export interface PerfSummary {
  fps?: number;
  frameMs?: { min: number; avg: number; max: number };
}

export interface AgentReportOptions {
  logs?: { limit?: number; level?: LogLevel; since?: number };
  /** Skip the per-entity component dump entirely. */
  skipEntities?: boolean;
  /** Also dump entities without a name (default: named entities only). */
  includeUnnamedEntities?: boolean;
  /** Cap on entities dumped with full components (default 100). */
  maxEntities?: number;
  /** Frame-rate section, supplied by the overlay-aware caller. */
  perf?: PerfSummary;
}

export interface AgentReport {
  meta: Record<string, unknown>;
  world: Record<string, unknown>;
  performance?: PerfSummary;
  loading: Record<string, unknown>;
  errors: ReturnType<typeof aggregateDiagnostics>;
  logs: DiagnosticEntry[];
  logsTruncated: boolean;
  assets: AssetsSummary;
  entities?: Array<{
    eid: number;
    name: string | null;
    components: Record<string, Record<string, number>>;
  }>;
  entitiesTruncated?: boolean;
  registry: {
    vars: Record<string, string>;
    actions: Array<{ name: string; description?: string }>;
  };
}

const MAX_ERRORS = 20;
const MAX_ASSETS_PER_LIST = 20;
const SLOW_RESOURCE_MS = 1000;
const OVERSIZED_RESOURCE_BYTES = 1024 * 1024;

export interface AssetsSummary {
  failed: Array<{ message: string; url?: string }>;
  slow: ResourceTimingEntry[];
  oversized: ResourceTimingEntry[];
  /** Loads with no payload — often a 404 served as a page, or cache hits. */
  zeroByte: ResourceTimingEntry[];
  observed: number;
}

export function buildAssetsSummary(): AssetsSummary {
  const timings = getResourceTimings();
  const failed = getDiagnostics({ kinds: ['resource'] })
    .slice(-MAX_ASSETS_PER_LIST)
    .map((e) => ({ message: e.message, url: e.url }));
  const pick = (
    pred: (t: ResourceTimingEntry) => boolean
  ): ResourceTimingEntry[] => timings.filter(pred).slice(-MAX_ASSETS_PER_LIST);
  return {
    failed,
    slow: pick((t) => t.durationMs > SLOW_RESOURCE_MS),
    oversized: pick((t) => t.decodedBytes > OVERSIZED_RESOURCE_BYTES),
    zeroByte: pick((t) => t.transferBytes === 0 && t.decodedBytes === 0),
    observed: timings.length,
  };
}

function buildMeta(): Record<string, unknown> {
  const meta: Record<string, unknown> = {
    timestamp: new Date().toISOString(),
    dev: import.meta.env.DEV !== false,
    headless: typeof window === 'undefined',
  };
  if (typeof window === 'undefined') return meta;
  meta.url = window.location.href;
  if (navigator.onLine === false) meta.online = false;
  if (navigator.hardwareConcurrency) {
    meta.hardwareConcurrency = navigator.hardwareConcurrency;
  }
  const memory = (
    performance as Performance & {
      memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number };
    }
  ).memory;
  if (memory) {
    meta.jsHeapUsedMB = +(memory.usedJSHeapSize / 1048576).toFixed(1);
    meta.jsHeapLimitMB = +(memory.jsHeapSizeLimit / 1048576).toFixed(1);
  }
  return meta;
}

function buildEntityDump(
  state: State,
  options: AgentReportOptions
): {
  entities: AgentReport['entities'];
  truncated: boolean;
} {
  const max = options.maxEntities ?? 100;
  const entities: NonNullable<AgentReport['entities']> = [];
  let truncated = false;

  const dump = (eid: number, name: string | null) => {
    if (entities.length >= max) {
      truncated = true;
      return;
    }
    entities.push({ eid, name, components: extractAllComponents(state, eid) });
  };

  for (const [name, eid] of state.getNamedEntities()) dump(eid, name);

  if (options.includeUnnamedEntities) {
    for (const eid of getAllEntities(state.world)) {
      if (state.getEntityName(eid) !== undefined) continue;
      dump(eid, null);
    }
  }

  return { entities, truncated };
}

export function buildAgentReport(
  state: State,
  options?: AgentReportOptions
): AgentReport {
  const opts = options ?? {};
  const logLimit = opts.logs?.limit ?? 40;
  const allLogs = getDiagnostics({
    kinds: ['console'],
    levels: opts.logs?.level ? [opts.logs.level] : undefined,
    since: opts.logs?.since,
  });

  const report: AgentReport = {
    meta: buildMeta(),
    world: {
      elapsed: +state.time.elapsed.toFixed(2),
      frameCount: state.time.frameCount,
      entities: getAllEntities(state.world).length,
      namedEntities: state.getNamedEntities().size,
      systems: state.systems.size,
      coroutines: getTotalActiveCoroutineCount(state),
      gltfLoadsInFlight: getActiveGltfLoadCount(),
    },
    loading: {
      physicsHeld: isPhysicsHeld(state),
      ...getLoadingProgress(state),
    },
    errors: aggregateDiagnostics(
      getDiagnostics({
        kinds: ['uncaught', 'unhandledrejection', 'webgl', 'resource'],
      })
    ).slice(0, MAX_ERRORS),
    logs: allLogs.slice(-logLimit),
    logsTruncated: allLogs.length > logLimit,
    assets: buildAssetsSummary(),
    registry: buildRegistrySummary(state),
  };
  if (opts.perf) report.performance = opts.perf;
  if (!opts.skipEntities) {
    const { entities, truncated } = buildEntityDump(state, opts);
    report.entities = entities;
    if (truncated) report.entitiesTruncated = true;
  }
  return report;
}

function buildRegistrySummary(state: State): AgentReport['registry'] {
  const reg = getDebugRegistry(state);
  const vars: Record<string, string> = {};
  for (const [name, entry] of reg.vars) {
    vars[name] = formatDebugValue(entry.get());
  }
  const actions = Array.from(reg.actions.values()).map((a) => ({
    name: a.name,
    description: a.description,
  }));
  return { vars, actions };
}
