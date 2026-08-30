import { afterEach, beforeEach, describe, expect, it } from 'bun:test';
import {
  aggregateDiagnostics,
  clearDiagnostics,
  diagnosticsCursor,
  getDiagnostics,
  installDiagnostics,
  isDiagnosticsInstalled,
  recordDiagnostic,
  serializeLogArgs,
} from '../../../src/plugins/debug/diagnostics';

// The diagnostics buffer is a page-global singleton shared with every other
// test file in the process, so every assertion projects on unique message
// markers instead of assuming a fresh buffer.
beforeEach(() => clearDiagnostics());

describe('diagnostics capture (node environment)', () => {
  it('is a safe no-op without a browser window', () => {
    const before = diagnosticsCursor();
    expect(installDiagnostics()).toBe(false);
    expect(isDiagnosticsInstalled()).toBe(false);
    expect(diagnosticsCursor()).toBe(before);
  });

  it('records engine-side diagnostics with a monotonic cursor', () => {
    const a = recordDiagnostic('uncaught', 'dg:boom', { stack: 'Error: boom' });
    const b = recordDiagnostic('resource', 'dg:missing', { url: '/x.png' });

    expect(b.seq).toBe(a.seq + 1);
    expect(diagnosticsCursor()).toBeGreaterThanOrEqual(b.seq);
    expect(getDiagnostics().filter((e) => e.message.startsWith('dg:'))).toEqual(
      [a, b]
    );
    expect(getDiagnostics({ since: a.seq - 1 })).toEqual(
      expect.arrayContaining([a, b])
    );
    expect(a).toMatchObject({ kind: 'uncaught', stack: 'Error: boom' });
    expect(b).toMatchObject({ kind: 'resource', url: '/x.png' });
  });

  it('filters by level, kinds, newestFirst and limit', () => {
    recordDiagnostic('console', 'dg/f:a', { level: 'info' });
    recordDiagnostic('console', 'dg/f:b', { level: 'warn' });
    recordDiagnostic('console', 'dg/f:c', { level: 'error' });
    recordDiagnostic('uncaught', 'dg/f:boom');

    const mine = (entries: ReturnType<typeof getDiagnostics>) =>
      entries.filter((e) => e.message.startsWith('dg/f:'));

    expect(mine(getDiagnostics({ levels: ['error'] }))).toHaveLength(1);
    expect(mine(getDiagnostics({ levels: ['warn', 'error'] }))).toHaveLength(2);
    expect(
      mine(getDiagnostics({ kinds: ['uncaught'], levels: ['error'] }))
    ).toHaveLength(0);
    expect(
      mine(getDiagnostics({ newestFirst: true })).map((e) => e.message)
    ).toEqual(['dg/f:boom', 'dg/f:c', 'dg/f:b', 'dg/f:a']);
    expect(getDiagnostics({ limit: 2 })).toHaveLength(2);
    expect(getDiagnostics({ newestFirst: true, limit: 2 })).toHaveLength(2);
    expect(mine(getDiagnostics({ kinds: ['uncaught'] }))).toHaveLength(1);
  });

  it('aggregates repeated diagnostics with counts', () => {
    recordDiagnostic('webgl', 'dg:Shader compile failed');
    recordDiagnostic('webgl', 'dg:Shader compile failed');
    recordDiagnostic('webgl', 'dg:Shader compile failed');
    recordDiagnostic('uncaught', 'dg:boom2');

    const summary = aggregateDiagnostics(
      getDiagnostics().filter((e) => e.message.startsWith('dg:'))
    );
    expect(summary).toHaveLength(2);
    expect(summary[0].count).toBe(3);
    expect(summary[0].kind).toBe('webgl');
    expect(summary[1].message).toBe('dg:boom2');
  });

  it('serializes console args losslessly enough for agents', () => {
    expect(serializeLogArgs(['hello', 'world'])).toBe('hello world');
    expect(serializeLogArgs([{ x: 1 }])).toBe('{"x":1}');
    expect(serializeLogArgs([new Error('bad')])).toContain('Error: bad');

    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(serializeLogArgs([circular])).toContain('[object Object]');
  });
});

describe('diagnostics capture (fake browser)', () => {
  const originalWindow = globalThis.window;
  const originalConsole = globalThis.console;
  const handlers = new Map<string, (event: unknown) => void>();

  function fakeWindow(): Window {
    const w = {
      addEventListener: (type: string, handler: (event: unknown) => void) => {
        handlers.set(type, handler);
      },
    } as unknown as Window;
    (globalThis as Record<string, unknown>).window = w;
    return w;
  }

  afterEach(() => {
    (globalThis as Record<string, unknown>).window = originalWindow;
    globalThis.console = originalConsole;
  });

  it('captures console output through the wrapped methods', () => {
    fakeWindow();
    const calls: string[] = [];
    globalThis.console = {
      log: (...args: unknown[]) => calls.push(String(args[0])),
      warn: () => {},
      error: () => {},
      debug: () => {},
      info: () => {},
    } as unknown as Console;

    expect(installDiagnostics()).toBe(true);
    console.warn('watch out', { code: 7 });
    console.log('still prints');

    expect(calls).toContain('still prints');
    const mine = getDiagnostics({ kinds: ['console'] }).slice(-2);
    expect(mine.map((e) => e.level)).toEqual(['warn', 'info']);
    expect(mine[0].message).toBe('watch out {"code":7}');
  });

  it('captures uncaught errors, resource failures and rejections', () => {
    fakeWindow();
    installDiagnostics();

    handlers.get('error')!({
      message: 'ReferenceError: x is not defined',
      error: { message: 'ReferenceError: x is not defined', stack: 'E: boom' },
    });
    handlers.get('error')!({
      target: { tagName: 'IMG', src: 'http://localhost/missing.glb' },
    });
    handlers.get('unhandledrejection')!({ reason: 'timeout' });

    const mine = getDiagnostics().filter(
      (e) =>
        e.message.includes('ReferenceError') ||
        e.message.includes('missing.glb') ||
        e.message === 'timeout'
    );
    expect(mine).toHaveLength(3);
    const [uncaught, resource, rejection] = mine;
    expect(uncaught.kind).toBe('uncaught');
    expect(uncaught.stack).toBe('E: boom');
    expect(resource.kind).toBe('resource');
    expect(resource.url).toBe('http://localhost/missing.glb');
    expect(rejection.kind).toBe('unhandledrejection');
    expect(rejection.message).toBe('timeout');
  });
});
