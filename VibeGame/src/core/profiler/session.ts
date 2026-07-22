import type { System } from '../ecs/types';

/** Profiler sampling mode. `off` keeps the hot path free of timing work. */
export type ProfilerMode = 'off' | 'sample' | 'deep';

/** Frame phases timed by the scheduler / runtime, plus manual custom spans. */
export type ProfilerGroup =
  | 'setup'
  | 'fixed'
  | 'simulation'
  | 'late'
  | 'draw'
  | 'render'
  | 'custom';

export interface ProfilerTimingStats {
  name: string;
  group: ProfilerGroup;
  /** Average milliseconds over the ring window. */
  avgMs: number;
  minMs: number;
  maxMs: number;
  /** 95th percentile over the ring window. */
  p95Ms: number;
  /** Most recent committed sample (ms). */
  lastMs: number;
  /** Share of average frame time (0–100). */
  pct: number;
  samples: number;
  /**
   * Best-effort source of the system (e.g. `plugins/gltf-xml/auto-instance.ts`
   * or `app/simple-rpg/src/main.ts`) captured at registration time.
   */
  origin: string;
}

export interface ProfilerGroupStats {
  group: ProfilerGroup;
  avgMs: number;
  minMs: number;
  maxMs: number;
  pct: number;
}

export interface ProfilerSnapshot {
  mode: ProfilerMode;
  frameCount: number;
  windowFrames: number;
  fps: number;
  frameAvgMs: number;
  frameMinMs: number;
  frameMaxMs: number;
  /** 95th percentile frame time over the ring window. */
  frameP95Ms: number;
  groups: ProfilerGroupStats[];
  systems: ProfilerTimingStats[];
  customs: ProfilerTimingStats[];
  frozen: boolean;
  timestamp: number;
}

const RING_SIZE = 120;
const GROUPS: ProfilerGroup[] = [
  'setup',
  'fixed',
  'simulation',
  'late',
  'draw',
  'render',
  'custom',
];

interface TimingAccum {
  name: string;
  group: ProfilerGroup;
  ring: Float64Array;
  samples: number;
  lastMs: number;
  origin: string;
}

interface FrameScratch {
  systems: Map<string, number>;
  groups: Map<ProfilerGroup, number>;
  customs: Map<string, number>;
}

let mode: ProfilerMode = 'off';
let enabled = false;
let frozen = false;
let frozenSnapshot: ProfilerSnapshot | null = null;
/** When true, {@link Scheduler} skips begin/end so the runtime can include the render pass. */
let externalFrame = false;

let frameIndex = 0;
let framesFilled = 0;
let totalFrames = 0;

const frameMsRing = new Float64Array(RING_SIZE);
const systemAccums = new Map<string, TimingAccum>();
const customAccums = new Map<string, TimingAccum>();
const groupRings = new Map<ProfilerGroup, Float64Array>();
const systemGroupByName = new Map<string, ProfilerGroup>();

for (const g of GROUPS) {
  groupRings.set(g, new Float64Array(RING_SIZE));
}

let scratch: FrameScratch = {
  systems: new Map(),
  groups: new Map(),
  customs: new Map(),
};

const openSpans = new Map<string, number>();
const systemNameCache = new WeakMap<System, string>();
const systemOriginCache = new WeakMap<System, string>();
const originBySystemName = new Map<string, string>();
let anonCounter = 0;

function ensureAccum(
  map: Map<string, TimingAccum>,
  name: string,
  group: ProfilerGroup,
  origin = 'unknown'
): TimingAccum {
  let acc = map.get(name);
  if (!acc) {
    acc = {
      name,
      group,
      ring: new Float64Array(RING_SIZE),
      samples: 0,
      lastMs: 0,
      origin,
    };
    map.set(name, acc);
  } else {
    acc.group = group;
    if ((!acc.origin || acc.origin === 'unknown') && origin !== 'unknown') {
      acc.origin = origin;
    }
  }
  return acc;
}

/** Strip Vite query/hash and normalize slashes for stack-frame parsing. */
function cleanStackLine(line: string): string {
  return line.replace(/\\/g, '/').replace(/\?[^:\s]*/g, '').replace(/#[^:\s]*/g, '');
}

/**
 * Best-effort call-site path for profiler labels.
 * Skips profiler/register frames so {@link defineSystem} resolves to the
 * defining module (e.g. `plugins/gltf-xml/auto-instance.ts`), not `builder.ts`.
 */
export function captureCallerOrigin(skip = 0): string {
  let stack: string | undefined;
  try {
    stack = new Error().stack;
  } catch {
    return 'unknown';
  }
  if (!stack) return 'unknown';
  const lines = stack.split('\n');
  let skipped = 0;
  for (const raw of lines) {
    const line = cleanStackLine(raw);
    if (!line.includes('.ts') && !line.includes('.js') && !line.includes('.tsx')) {
      continue;
    }
    if (
      line.includes('profiler/session') ||
      line.includes('ecs/state.ts') ||
      line.includes('ecs/scheduler') ||
      line.includes('node_modules') ||
      line.includes('node:') ||
      line.includes('native:') ||
      line.includes('[eval]')
    ) {
      continue;
    }
    if (skipped < skip) {
      skipped += 1;
      continue;
    }
    const plugins = line.match(/plugins\/([\w-]+\/[\w./-]+\.tsx?)/);
    if (plugins) return `plugins/${plugins[1]}`;
    const app = line.match(/examples\/([\w-]+)\/src\/([\w./-]+\.tsx?)/);
    if (app) return `app/${app[1]}/${app[2]}`;
    const core = line.match(/\/src\/(core\/[\w./-]+\.tsx?)/);
    if (core) return core[1];
    const src = line.match(/\/src\/([\w./-]+\.tsx?)/);
    if (src) return src[1];
    const tests = line.match(/\/(tests\/[\w./-]+\.tsx?)/);
    if (tests) return tests[1];
    // Bun / Node absolute paths (last resort)
    const abs = line.match(/((?:\/[\w.-]+)+\.tsx?):/);
    if (abs) {
      const path = abs[1]!;
      const cut = path.indexOf('/VibeGame/');
      if (cut >= 0) return path.slice(cut + '/VibeGame/'.length);
      return path.split('/').slice(-3).join('/');
    }
  }
  return 'unknown';
}

function shortOrigin(origin: string): string {
  return origin
    .replace(/^plugins\//, '')
    .replace(/^app\//, 'app/')
    .replace(/\.ts$/, '');
}

function markDeep(label: string, end: boolean): void {
  if (mode !== 'deep' || typeof performance === 'undefined') return;
  try {
    if (end) {
      const startMark = `vg:${label}:start`;
      const endMark = `vg:${label}:end`;
      performance.mark(endMark);
      performance.measure(`vg:${label}`, startMark, endMark);
    } else {
      performance.mark(`vg:${label}:start`);
    }
  } catch {
    // User Timing can throw if marks collide; never break the frame loop.
  }
}

/** Fast hot-path gate used by the scheduler / runtime. */
export function isProfilerEnabled(): boolean {
  return enabled;
}

export function getProfilerMode(): ProfilerMode {
  return mode;
}

export function isProfilerFrozen(): boolean {
  return frozen;
}

/**
 * Enable profiling. `sample` times systems/groups; `deep` also emits
 * `performance.mark` / `measure` for the Chrome Performance panel.
 */
export function enableProfiler(next: ProfilerMode = 'sample'): void {
  if (next === 'off') {
    disableProfiler();
    return;
  }
  mode = next;
  enabled = true;
  frozen = false;
  frozenSnapshot = null;
}

export function disableProfiler(): void {
  mode = 'off';
  enabled = false;
  frozen = false;
  frozenSnapshot = null;
  externalFrame = false;
  openSpans.clear();
}

export function setProfilerMode(next: ProfilerMode): void {
  if (next === 'off') {
    disableProfiler();
    return;
  }
  mode = next;
  enabled = true;
}

export function freezeProfiler(): ProfilerSnapshot {
  frozenSnapshot = buildSnapshot();
  frozen = true;
  return frozenSnapshot;
}

export function unfreezeProfiler(): void {
  frozen = false;
  frozenSnapshot = null;
}

export function toggleProfilerFreeze(): boolean {
  if (frozen) {
    unfreezeProfiler();
    return false;
  }
  freezeProfiler();
  return true;
}

function usableFnName(fn: ((...args: never[]) => unknown) | undefined): string | null {
  if (!fn?.name) return null;
  const n = fn.name;
  if (
    !n ||
    n === 'update' ||
    n === 'setup' ||
    n === 'dispose' ||
    n === 'anonymous' ||
    n.startsWith('_')
  ) {
    return null;
  }
  return n;
}

/** Resolve a stable display name for a system object. */
export function resolveSystemName(system: System): string {
  const cached = systemNameCache.get(system);
  if (cached) return cached;

  let name = system.name?.trim() || '';
  if (!name) name = usableFnName(system.update) ?? '';
  if (!name) name = usableFnName(system.setup) ?? '';
  if (!name) {
    anonCounter += 1;
    const origin =
      systemOriginCache.get(system) ?? captureCallerOrigin();
    systemOriginCache.set(system, origin);
    name = `unnamed:${shortOrigin(origin)}#${anonCounter}`;
  }
  systemNameCache.set(system, name);
  const origin = systemOriginCache.get(system);
  if (origin) originBySystemName.set(name, origin);
  return name;
}

/** Attach an explicit display name (used by {@link namedSystem}). */
export function setSystemProfilerName(system: System, name: string): void {
  systemNameCache.set(system, name);
}

/**
 * Call from {@link State.registerSystem} so unnamed systems get a descriptive
 * fallback (`unnamed:gltf-xml/auto-instance#3`) and an origin path for the panel.
 * Prefer {@link defineSystem} at the definition site so origin is the source file.
 */
export function noteSystemRegistration(system: System): void {
  if (!systemOriginCache.has(system)) {
    systemOriginCache.set(system, captureCallerOrigin());
  }
  const name = resolveSystemName(system);
  const origin = systemOriginCache.get(system) ?? 'unknown';
  originBySystemName.set(name, origin);
}

/**
 * Wrap a system at its definition site so the profiler records the source file
 * (not the `registerPlugin` / builder call stack). Prefer this for all engine systems.
 *
 * @example
 * export const FooSystem: System = defineSystem({
 *   name: 'FooSystem',
 *   update(state) { ... },
 * });
 */
export function defineSystem(system: System): System {
  if (!systemOriginCache.has(system)) {
    systemOriginCache.set(system, captureCallerOrigin());
  }
  const origin = systemOriginCache.get(system) ?? 'unknown';
  if (system.name?.trim()) {
    const name = system.name.trim();
    systemNameCache.set(system, name);
    originBySystemName.set(name, origin);
  }
  return system;
}

/**
 * Return a system copy with an explicit `name` for profiler labelling.
 * Prefer setting `name` on the system literal when defining engine systems.
 */
export function namedSystem(name: string, system: System): System {
  const named: System = { ...system, name };
  setSystemProfilerName(named, name);
  const origin =
    systemOriginCache.get(system) ??
    systemOriginCache.get(named) ??
    captureCallerOrigin();
  systemOriginCache.set(named, origin);
  originBySystemName.set(name, origin);
  return named;
}

/**
 * Open a frame owned by GameRuntime so the WebGL submit can be timed before
 * commit. Pair with {@link endExternalProfilerFrame}.
 */
export function beginExternalProfilerFrame(): void {
  if (!enabled || frozen) return;
  externalFrame = true;
  beginProfilerFrame();
}

export function endExternalProfilerFrame(frameDeltaSeconds: number): void {
  if (!externalFrame) return;
  if (enabled) endProfilerFrame(frameDeltaSeconds);
  externalFrame = false;
}

export function isExternalProfilerFrame(): boolean {
  return externalFrame;
}

/** Call at the start of each frame (before scheduler groups). */
export function beginProfilerFrame(): void {
  if (!enabled || frozen) return;
  scratch = {
    systems: new Map(),
    groups: new Map(),
    customs: new Map(),
  };
  openSpans.clear();
}

/** Record one system update duration (milliseconds). */
export function recordSystemTiming(
  group: Exclude<ProfilerGroup, 'render' | 'custom'>,
  name: string,
  ms: number
): void {
  if (!enabled || frozen) return;
  scratch.systems.set(name, (scratch.systems.get(name) ?? 0) + ms);
  scratch.groups.set(group, (scratch.groups.get(group) ?? 0) + ms);
  systemGroupByName.set(name, group);
}

/** Record the WebGL / postprocessing submit pass. */
export function recordRenderTiming(ms: number): void {
  if (!enabled || frozen) return;
  scratch.groups.set('render', (scratch.groups.get('render') ?? 0) + ms);
}

/**
 * Accumulate a custom span duration without begin/end pairing.
 * Use when many call sites share one label (e.g. per-entity script updates
 * rolled up as `script/creature`).
 */
export function recordCustomTiming(
  name: string,
  ms: number,
  origin = 'custom-span'
): void {
  if (!enabled || frozen || ms <= 0) return;
  scratch.customs.set(name, (scratch.customs.get(name) ?? 0) + ms);
  if (origin && origin !== 'unknown') {
    originBySystemName.set(name, origin);
  }
}

/** Begin a manual span (game code). Nested same-name spans are not supported. */
export function beginSpan(name: string): void {
  if (!enabled || frozen) return;
  openSpans.set(name, performance.now());
  markDeep(`custom/${name}`, false);
}

/** End a manual span started with {@link beginSpan}. */
export function endSpan(name: string): void {
  if (!enabled || frozen) return;
  const t0 = openSpans.get(name);
  if (t0 === undefined) return;
  openSpans.delete(name);
  const ms = performance.now() - t0;
  // Customs nest inside systems; keep them out of group totals so bars stay ≤100%.
  scratch.customs.set(name, (scratch.customs.get(name) ?? 0) + ms);
  markDeep(`custom/${name}`, true);
}

/** Time a synchronous callback as a custom span. */
export function withSpan<T>(name: string, fn: () => T): T {
  if (!enabled || frozen) return fn();
  beginSpan(name);
  try {
    return fn();
  } finally {
    endSpan(name);
  }
}

/** Scheduler helper: time a system update with optional deep marks. */
export function profileSystemUpdate(
  group: Exclude<ProfilerGroup, 'render' | 'custom'>,
  system: System,
  run: () => void
): void {
  if (!enabled || frozen) {
    run();
    return;
  }
  const name = resolveSystemName(system);
  const label = `${group}/${name}`;
  if (mode === 'deep') markDeep(label, false);
  const t0 = performance.now();
  run();
  const ms = performance.now() - t0;
  if (mode === 'deep') markDeep(label, true);
  recordSystemTiming(group, name, ms);
}

/** Time the render pass with optional deep marks. */
export function profileRenderPass(run: () => void): void {
  if (!enabled || frozen) {
    run();
    return;
  }
  if (mode === 'deep') markDeep('render', false);
  const t0 = performance.now();
  run();
  const ms = performance.now() - t0;
  if (mode === 'deep') markDeep('render', true);
  recordRenderTiming(ms);
}

/** Commit the current frame scratch into the ring buffer. */
export function endProfilerFrame(frameDeltaSeconds: number): void {
  if (!enabled || frozen) return;

  const frameMs = frameDeltaSeconds > 0 ? frameDeltaSeconds * 1000 : 0;
  const idx = frameIndex;

  frameMsRing[idx] = frameMs;

  for (const g of GROUPS) {
    const ring = groupRings.get(g)!;
    ring[idx] = scratch.groups.get(g) ?? 0;
  }

  for (const acc of systemAccums.values()) {
    acc.ring[idx] = 0;
  }
  for (const [name, ms] of scratch.systems) {
    const group = systemGroupByName.get(name) ?? 'simulation';
    const origin = originBySystemName.get(name) ?? 'unknown';
    const acc = ensureAccum(systemAccums, name, group, origin);
    acc.ring[idx] = ms;
    acc.lastMs = ms;
    acc.samples += 1;
  }

  for (const acc of customAccums.values()) {
    acc.ring[idx] = 0;
  }
  for (const [name, ms] of scratch.customs) {
    const acc = ensureAccum(customAccums, name, 'custom', 'custom-span');
    acc.ring[idx] = ms;
    acc.lastMs = ms;
    acc.samples += 1;
  }

  frameIndex = (idx + 1) % RING_SIZE;
  if (framesFilled < RING_SIZE) framesFilled += 1;
  totalFrames += 1;
}

function ringStats(
  ring: Float64Array,
  count: number
): { avg: number; min: number; max: number; p95: number } {
  if (count <= 0) return { avg: 0, min: 0, max: 0, p95: 0 };
  let sum = 0;
  let min = Number.POSITIVE_INFINITY;
  let max = 0;
  const vals: number[] = [];
  for (let i = 0; i < count; i++) {
    const v = ring[i];
    vals.push(v);
    sum += v;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  if (min === Number.POSITIVE_INFINITY) min = 0;
  vals.sort((a, b) => a - b);
  const p95Index = Math.min(
    vals.length - 1,
    Math.max(0, Math.ceil(vals.length * 0.95) - 1)
  );
  return { avg: sum / count, min, max, p95: vals[p95Index]! };
}

function toTimingStats(
  acc: TimingAccum,
  frameAvg: number
): ProfilerTimingStats {
  const { avg, min, max, p95 } = ringStats(acc.ring, framesFilled);
  return {
    name: acc.name,
    group: systemGroupByName.get(acc.name) ?? acc.group,
    avgMs: avg,
    minMs: min,
    maxMs: max,
    p95Ms: p95,
    lastMs: acc.lastMs,
    pct: frameAvg > 0 ? (avg / frameAvg) * 100 : 0,
    samples: acc.samples,
    origin:
      acc.origin !== 'unknown'
        ? acc.origin
        : (originBySystemName.get(acc.name) ?? 'unknown'),
  };
}

function buildSnapshot(): ProfilerSnapshot {
  const frame = ringStats(frameMsRing, framesFilled);
  const groups: ProfilerGroupStats[] = GROUPS.map((group) => {
    const { avg, min, max } = ringStats(groupRings.get(group)!, framesFilled);
    return {
      group,
      avgMs: avg,
      minMs: min,
      maxMs: max,
      pct: frame.avg > 0 ? (avg / frame.avg) * 100 : 0,
    };
  });

  const systems = Array.from(systemAccums.values())
    .map((acc) => toTimingStats(acc, frame.avg))
    .filter((s) => s.samples > 0 || s.avgMs > 0)
    .sort((a, b) => b.avgMs - a.avgMs);

  const customs = Array.from(customAccums.values())
    .map((acc) => toTimingStats(acc, frame.avg))
    .filter((s) => s.samples > 0 || s.avgMs > 0)
    .sort((a, b) => b.avgMs - a.avgMs);

  const fps = frame.avg > 0 ? 1000 / frame.avg : 0;

  return {
    mode,
    frameCount: totalFrames,
    windowFrames: framesFilled,
    fps,
    frameAvgMs: frame.avg,
    frameMinMs: frame.min,
    frameMaxMs: frame.max,
    frameP95Ms: frame.p95,
    groups,
    systems,
    customs,
    frozen,
    timestamp: Date.now(),
  };
}

export function getProfilerSnapshot(): ProfilerSnapshot {
  if (frozen && frozenSnapshot) return frozenSnapshot;
  return buildSnapshot();
}

export function getProfilerTop(n = 15): ProfilerTimingStats[] {
  const snap = getProfilerSnapshot();
  return snap.systems.slice(0, Math.max(0, n));
}

/**
 * Reset rings and counters (keeps mode/enabled).
 * Keeps registration origins / name caches so the panel stays descriptive after Reset.
 */
export function resetProfiler(): void {
  frameIndex = 0;
  framesFilled = 0;
  totalFrames = 0;
  frameMsRing.fill(0);
  for (const ring of groupRings.values()) ring.fill(0);
  systemAccums.clear();
  customAccums.clear();
  systemGroupByName.clear();
  openSpans.clear();
  frozen = false;
  frozenSnapshot = null;
  externalFrame = false;
  scratch = {
    systems: new Map(),
    groups: new Map(),
    customs: new Map(),
  };
}

/** Test-only: wipe all profiler global state including mode and origins. */
export function _resetProfilerForTests(): void {
  disableProfiler();
  resetProfiler();
  originBySystemName.clear();
  anonCounter = 0;
}

/** Look up registration origin for a resolved system/custom name. */
export function getProfilerOrigin(name: string): string {
  return originBySystemName.get(name) ?? 'unknown';
}

export function downloadProfilerSnapshot(filename?: string): ProfilerSnapshot {
  const snap = getProfilerSnapshot();
  if (typeof document === 'undefined') return snap;
  const name =
    filename ??
    `vibegame-profile-${new Date().toISOString().replace(/[:.]/g, '-')}.json`;
  const blob = new Blob([JSON.stringify(snap, null, 2)], {
    type: 'application/json',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
  return snap;
}

export async function copyProfilerSnapshot(): Promise<boolean> {
  const snap = getProfilerSnapshot();
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(JSON.stringify(snap, null, 2));
      return true;
    }
  } catch {
    return false;
  }
  return false;
}
