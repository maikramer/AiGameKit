import type { LogLevel } from '../../core/utils/logger';

/**
 * In-page diagnostic capture for AI-agent debugging over browser MCP.
 *
 * Every entry gets a monotonic `seq`, so an agent can poll
 * `__VIBEGAME__.logs({ since })` and never miss or re-read a message —
 * unlike the MCP's own console capture, which resets on navigation and
 * cannot see messages emitted before it attached.
 *
 * Captured kinds: `console` (all levels), `uncaught` errors, `unhandledrejection`,
 * `webgl` shader/program failures, and `resource` load failures (<img>, <script>,
 * <audio>, <link>). Page-global and idempotent: one install covers every world
 * reload. Error-kind entries are also forwarded over the Vite HMR console
 * channel so the dev-server terminal and `.vibegame/console.log` get durable
 * copies.
 */

export type DiagnosticKind =
  'console' | 'uncaught' | 'unhandledrejection' | 'webgl' | 'resource';

export interface DiagnosticEntry {
  seq: number;
  /** Epoch ms. */
  ts: number;
  kind: DiagnosticKind;
  /** Console level; present only when `kind === 'console'`. */
  level?: LogLevel;
  message: string;
  detail?: string;
  stack?: string;
  /** Resource URL; present only when `kind === 'resource'`. */
  url?: string;
}

export interface ResourceTimingEntry {
  url: string;
  /** Epoch ms of the load start. */
  ts: number;
  durationMs: number;
  transferBytes: number;
  decodedBytes: number;
}

export interface DiagnosticsFilter {
  /** Only entries with `seq > since`. */
  since?: number;
  kinds?: DiagnosticKind[];
  levels?: LogLevel[];
  /** Newest-first is the storage order; default returns oldest-first. */
  newestFirst?: boolean;
  limit?: number;
}

const MAX_ENTRIES = 500;
const MAX_RESOURCE_TIMINGS = 200;
const MAX_MESSAGE_LENGTH = 600;
const MAX_STACK_LENGTH = 2000;

const state = {
  installed: false,
  seq: 0,
  entries: [] as DiagnosticEntry[],
  resourceTimings: [] as ResourceTimingEntry[],
};

export function isDiagnosticsInstalled(): boolean {
  return state.installed;
}

export function diagnosticsCursor(): number {
  return state.seq;
}

export function clearDiagnostics(): void {
  state.entries = [];
  state.resourceTimings = [];
}

export function getDiagnostics(filter?: DiagnosticsFilter): DiagnosticEntry[] {
  let out = state.entries;
  if (filter?.since !== undefined) {
    out = out.filter((e) => e.seq > filter.since!);
  }
  if (filter?.kinds) {
    const kinds = new Set(filter.kinds);
    out = out.filter((e) => kinds.has(e.kind));
  }
  if (filter?.levels) {
    const levels = new Set(filter.levels);
    out = out.filter((e) => e.level != null && levels.has(e.level));
  }
  const limit = filter?.limit;
  if (filter?.newestFirst) {
    out = limit != null ? out.slice(-limit).reverse() : [...out].reverse();
  } else if (limit != null) {
    out = out.slice(-limit);
  }
  return out;
}

export function getResourceTimings(limit?: number): ResourceTimingEntry[] {
  return limit != null
    ? state.resourceTimings.slice(-limit)
    : [...state.resourceTimings];
}

function truncate(value: string, max: number): string {
  return value.length > max ? value.slice(0, max - 3) + '...' : value;
}

/** JSON-safe one-line rendering of console arguments for log capture. */
export function serializeLogArgs(args: unknown[]): string {
  return truncate(
    args
      .map((a) => {
        if (typeof a === 'string') return a;
        if (a instanceof Error) return a.stack || `${a.name}: ${a.message}`;
        try {
          return JSON.stringify(a) ?? String(a);
        } catch {
          return String(a);
        }
      })
      .join(' '),
    MAX_MESSAGE_LENGTH
  );
}

function push(entry: Omit<DiagnosticEntry, 'seq' | 'ts'>): DiagnosticEntry {
  const full: DiagnosticEntry = { seq: ++state.seq, ts: Date.now(), ...entry };
  state.entries.push(full);
  if (state.entries.length > MAX_ENTRIES) {
    state.entries.splice(0, state.entries.length - MAX_ENTRIES);
  }
  return full;
}

/**
 * Forward an engine-captured error over the Vite HMR console channel so it
 * reaches the dev-server terminal and the `.vibegame/console.log` file.
 * No-op outside a Vite dev session (built libs have no `import.meta.hot`).
 */
function forwardToDevServer(entry: DiagnosticEntry): void {
  if (typeof import.meta === 'undefined' || !import.meta.hot) return;
  try {
    import.meta.hot.send('vibegame:console', {
      level: 'error',
      message: `[${entry.kind}] ${entry.message}`,
      args: entry.detail ? [entry.detail] : undefined,
      timestamp: entry.ts,
      context: { stack: entry.stack, source: entry.kind, url: entry.url },
    });
  } catch {
    // Socket not connected yet — the in-page buffer already has the entry.
  }
}

/** Record an engine-side diagnostic (world parse failure, plugin error, ...). */
export function recordDiagnostic(
  kind: DiagnosticKind,
  message: string,
  extra?: Partial<Pick<DiagnosticEntry, 'detail' | 'stack' | 'url' | 'level'>>
): DiagnosticEntry {
  const entry = push({
    kind,
    message: truncate(message, MAX_MESSAGE_LENGTH),
    ...extra,
  });
  if (kind !== 'console') forwardToDevServer(entry);
  return entry;
}

function recordConsole(level: LogLevel, args: unknown[]): void {
  push({ kind: 'console', level, message: serializeLogArgs(args) });
}

function patchWebglContext(): void {
  if (typeof HTMLCanvasElement === 'undefined') return;
  const proto = HTMLCanvasElement.prototype as unknown as {
    getContext: (...args: unknown[]) => unknown;
  };
  const origGetContext = proto.getContext;
  proto.getContext = function (this: HTMLCanvasElement, ...args: unknown[]) {
    const ctx = origGetContext.apply(this, args) as Record<
      string,
      unknown
    > | null;
    if (
      ctx &&
      typeof ctx.getError === 'function' &&
      !(ctx as { __vibePatched?: boolean }).__vibePatched
    ) {
      (ctx as { __vibePatched?: boolean }).__vibePatched = true;
      const gl = ctx as WebGL2RenderingContext &
        Record<string, (...a: unknown[]) => unknown>;
      const origCompileShader = gl.compileShader.bind(gl);
      gl.compileShader = (shader: WebGLShader) => {
        origCompileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          const info = gl.getShaderInfoLog(shader);
          recordDiagnostic('webgl', 'Shader compile failed', {
            detail: info ? truncate(info, MAX_STACK_LENGTH) : undefined,
          });
        }
      };
      const origLinkProgram = gl.linkProgram.bind(gl);
      gl.linkProgram = (program: WebGLProgram) => {
        origLinkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
          const info = gl.getProgramInfoLog(program);
          recordDiagnostic('webgl', 'Program link failed', {
            detail: info ? truncate(info, MAX_STACK_LENGTH) : undefined,
          });
        }
      };
    }
    return ctx;
  } as typeof proto.getContext;
}

function watchResourceTimings(): void {
  if (typeof PerformanceObserver === 'undefined') return;
  try {
    const observer = new PerformanceObserver((list) => {
      for (const item of list.getEntries()) {
        state.resourceTimings.push({
          url: item.name,
          ts: item.startTime,
          durationMs: item.duration,
          transferBytes: (item as PerformanceResourceTiming).transferSize ?? 0,
          decodedBytes:
            (item as PerformanceResourceTiming).decodedBodySize ?? 0,
        });
      }
      if (state.resourceTimings.length > MAX_RESOURCE_TIMINGS) {
        state.resourceTimings.splice(
          0,
          state.resourceTimings.length - MAX_RESOURCE_TIMINGS
        );
      }
    });
    observer.observe({ type: 'resource', buffered: true });
  } catch {
    // Unsupported entry type — resource timings stay empty.
  }
}

/**
 * Install every capture hook (console wrap, `error`, `unhandledrejection`,
 * WebGL context instrumentation, resource timing observer). Idempotent and
 * page-global: the buffer survives world hot-swaps so pre-reload errors stay
 * readable. Safe no-op outside a browser.
 */
export function installDiagnostics(): boolean {
  if (state.installed) return false;
  if (typeof window === 'undefined') return false;
  if (typeof window.addEventListener !== 'function') return false;

  patchWebglContext();
  watchResourceTimings();

  window.addEventListener(
    'error',
    (event: ErrorEvent) => {
      const target = event.target as EventTarget | null;
      if (target && target !== window && (target as Element).tagName) {
        const el = target as Element;
        const url =
          (el as HTMLImageElement).src ||
          (el as HTMLLinkElement).href ||
          el.getAttribute('src') ||
          '';
        recordDiagnostic(
          'resource',
          `Failed to load <${el.tagName.toLowerCase()}> ${url}`,
          { url }
        );
        return;
      }
      const err = event.error;
      recordDiagnostic(
        'uncaught',
        err?.message || event.message || 'Unknown error',
        {
          stack: err?.stack ? truncate(err.stack, MAX_STACK_LENGTH) : undefined,
        }
      );
    },
    true
  );

  window.addEventListener(
    'unhandledrejection',
    (event: PromiseRejectionEvent) => {
      const reason = event.reason;
      recordDiagnostic(
        'unhandledrejection',
        reason instanceof Error ? reason.message : String(reason),
        {
          stack:
            reason instanceof Error && reason.stack
              ? truncate(reason.stack, MAX_STACK_LENGTH)
              : undefined,
        }
      );
    }
  );

  // `console.log` records as `info` — same mapping as the Vite console
  // forwarder — so log entries always carry a valid LogLevel.
  const wrap = (
    method: 'debug' | 'info' | 'log' | 'warn' | 'error',
    level: LogLevel
  ) => {
    const original = console[method].bind(console);
    console[method] = (...args: unknown[]) => {
      original(...args);
      recordConsole(level, args);
    };
  };
  wrap('debug', 'debug');
  wrap('info', 'info');
  wrap('log', 'info');
  wrap('warn', 'warn');
  wrap('error', 'error');

  state.installed = true;
  return true;
}

export interface DiagnosticSummary {
  kind: DiagnosticKind;
  message: string;
  count: number;
  firstTs: number;
  lastTs: number;
  /** First-seen stack/detail sample, when present. */
  stack?: string;
  url?: string;
}

/** Aggregate repeated diagnostics into per-message counts (report-friendly). */
export function aggregateDiagnostics(
  entries: DiagnosticEntry[]
): DiagnosticSummary[] {
  const byKey = new Map<string, DiagnosticSummary>();
  for (const e of entries) {
    const key = `${e.kind}\u0000${e.message}`;
    const existing = byKey.get(key);
    if (existing) {
      existing.count += 1;
      existing.lastTs = e.ts;
    } else {
      byKey.set(key, {
        kind: e.kind,
        message: e.message,
        count: 1,
        firstTs: e.ts,
        lastTs: e.ts,
        stack: e.stack,
        url: e.url,
      });
    }
  }
  return Array.from(byKey.values()).sort(
    (a, b) => b.count - a.count || b.lastTs - a.lastTs
  );
}
