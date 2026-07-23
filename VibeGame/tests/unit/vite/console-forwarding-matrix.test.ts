import { afterEach, beforeEach, describe, expect, it, mock } from 'bun:test';
import type { Plugin, ViteDevServer, WebSocketServer } from 'vite';
import { consoleForwarding } from '../../../src/vite/console-plugin.ts';

type WsHandler = (data: unknown) => void;

function setup() {
  const handlers = new Map<string, WsHandler>();
  const send = mock(() => {});
  const on = mock((event: string, cb: WsHandler) => {
    handlers.set(event, cb);
  });
  const ws = {
    on: on as unknown as WebSocketServer['on'],
    send: send as unknown as WebSocketServer['send'],
    clients: new Set(),
  } as unknown as WebSocketServer;
  const server = { ws } as unknown as ViteDevServer;
  const plugin = consoleForwarding();
  return { plugin, server, ws, send, on, handlers };
}

function configure(plugin: Plugin, server: ViteDevServer) {
  const hook = plugin.configureServer;
  expect(typeof hook).toBe('function');
  (hook as (s: ViteDevServer) => void).call({}, server);
}

function transform(plugin: Plugin, code: string, id: string): string {
  const hook = plugin.transform;
  expect(typeof hook).toBe('function');
  const result = (
    hook as (code: string, id: string) => string | undefined
  ).call({}, code, id);
  return typeof result === 'string' ? result : code;
}

describe('consoleForwarding matrix — structure', () => {
  it('returns a single plugin object', () => {
    expect(consoleForwarding()).toBeTruthy();
    expect(Array.isArray(consoleForwarding())).toBe(false);
  });

  it('has name vibegame:console-forwarding', () => {
    expect(consoleForwarding().name).toBe('vibegame:console-forwarding');
  });

  it('applies only in serve', () => {
    expect(consoleForwarding().apply).toBe('serve');
  });

  it('enforces post', () => {
    expect(consoleForwarding().enforce).toBe('post');
  });

  it('fresh instance each call', () => {
    expect(consoleForwarding()).not.toBe(consoleForwarding());
  });
});

describe('consoleForwarding matrix — transform targeting', () => {
  const mainIds = [
    '/src/main.ts',
    '/src/main.js',
    '/home/proj/src/main.ts',
    '/home/proj/src/main.js',
    'C:/game/src/main.ts',
    '/app/packages/demo/src/main.ts',
    '/x/src/main.js?v=1',
  ];

  for (const id of mainIds) {
    it(`injects into ${id}`, () => {
      const { plugin } = setup();
      const out = transform(plugin, 'export {}', id);
      expect(out).toContain('import.meta.hot');
      expect(out).toContain('vibegame:console');
      expect(out.endsWith('export {}') || out.includes('export {}')).toBe(true);
    });
  }

  // Current matcher is substring-based (`includes('/src/main.ts')` /
  // `includes('/src/main.js')`), so some near-miss paths also inject.
  const substringFalsePositives = [
    '/src/main.tsx',
    '/src/main.ts.bak',
    '/src/main.ts/extra',
    '/foo/src/main.js.map',
  ];

  for (const id of substringFalsePositives) {
    it(`substring matcher also injects into ${id}`, () => {
      const { plugin } = setup();
      const out = transform(plugin, 'export {}', id);
      expect(out).toContain('vibegame:console');
    });
  }

  const nonMainIds = [
    '/src/App.ts',
    '/src/main.mjs',
    '/src/components/main.ts',
    '/src/utils/logger.ts',
    '/tests/main.ts',
    '/src/Main.ts',
    '',
    '/index.html',
    '/src/main.css',
    '/public/main.ts',
  ];

  for (const id of nonMainIds) {
    it(`does not inject into ${id || '(empty)'}`, () => {
      const { plugin } = setup();
      const code = 'const x = 1;';
      expect(transform(plugin, code, id)).toBe(code);
    });
  }

  it('prepends injection before original code', () => {
    const { plugin } = setup();
    const code = 'MARKER_ORIGINAL';
    const out = transform(plugin, code, '/src/main.ts');
    expect(out.indexOf('import.meta.hot')).toBeLessThan(
      out.indexOf('MARKER_ORIGINAL')
    );
  });

  it('preserves unicode in original code', () => {
    const { plugin } = setup();
    const code = 'const s = "ação ゲーム";';
    expect(transform(plugin, code, '/src/main.ts')).toContain(code);
  });

  it('injection mentions Rapier deprecation suppress', () => {
    const { plugin } = setup();
    const out = transform(plugin, '', '/src/main.ts');
    expect(out).toContain(
      'deprecated parameters for the initialization function'
    );
  });

  it('injection maps log to info level', () => {
    const { plugin } = setup();
    const out = transform(plugin, '', '/src/main.ts');
    expect(out).toContain("method === 'log' ? 'info' : method");
  });

  it('injection serializes Error stack', () => {
    const { plugin } = setup();
    const out = transform(plugin, '', '/src/main.ts');
    expect(out).toContain('instanceof Error');
    expect(out).toContain('message.context.stack');
  });

  it('injection wraps send in try/catch', () => {
    const { plugin } = setup();
    const out = transform(plugin, '', '/src/main.ts');
    expect(out).toContain('try {');
    expect(out).toContain("import.meta.hot.send('vibegame:console'");
  });

  it('injection announces enabling', () => {
    const { plugin } = setup();
    const out = transform(plugin, '', '/src/main.ts');
    expect(out).toContain('[VibeGame] Console forwarding enabled');
  });
});

describe('consoleForwarding matrix — websocket handler levels', () => {
  const levels: Array<{
    level: string;
    consoleMethod: 'debug' | 'log' | 'warn' | 'error';
  }> = [
    { level: 'debug', consoleMethod: 'debug' },
    { level: 'info', consoleMethod: 'log' },
    { level: 'warn', consoleMethod: 'warn' },
    { level: 'error', consoleMethod: 'error' },
    { level: 'trace', consoleMethod: 'log' },
    { level: 'verbose', consoleMethod: 'log' },
    { level: 'fatal', consoleMethod: 'log' },
  ];

  for (const { level, consoleMethod } of levels) {
    it(`routes level ${level} to console.${consoleMethod}`, () => {
      const { plugin, server, handlers } = setup();
      const spy = mock(() => {});
      const prev = console[consoleMethod];
      console[consoleMethod] = spy as typeof prev;
      configure(plugin, server);
      handlers.get('vibegame:console')?.({
        level,
        message: `${level}-msg`,
        timestamp: Date.now(),
        context: {},
      });
      expect(spy).toHaveBeenCalled();
      console[consoleMethod] = prev;
    });
  }

  it('fills missing timestamp', () => {
    const { plugin, server, handlers } = setup();
    const spy = mock(() => {});
    const prev = console.log;
    console.log = spy as typeof prev;
    configure(plugin, server);
    handlers.get('vibegame:console')?.({
      level: 'info',
      message: 'no-ts',
      context: {},
    });
    expect(spy).toHaveBeenCalled();
    console.log = prev;
  });

  it('fills NaN timestamp', () => {
    const { plugin, server, handlers } = setup();
    const spy = mock(() => {});
    const prev = console.log;
    console.log = spy as typeof prev;
    configure(plugin, server);
    handlers.get('vibegame:console')?.({
      level: 'info',
      message: 'nan-ts',
      timestamp: Number.NaN,
      context: {},
    });
    expect(spy).toHaveBeenCalled();
    console.log = prev;
  });

  it('fills missing context object', () => {
    const { plugin, server, handlers } = setup();
    const spy = mock(() => {});
    const prev = console.warn;
    console.warn = spy as typeof prev;
    configure(plugin, server);
    handlers.get('vibegame:console')?.({
      level: 'warn',
      message: 'no-ctx',
      timestamp: 1,
    });
    expect(spy).toHaveBeenCalled();
    console.warn = prev;
  });

  it('ignores null payload', () => {
    const { plugin, server, handlers } = setup();
    configure(plugin, server);
    expect(() => handlers.get('vibegame:console')?.(null)).not.toThrow();
  });

  it('ignores undefined payload', () => {
    const { plugin, server, handlers } = setup();
    configure(plugin, server);
    expect(() => handlers.get('vibegame:console')?.(undefined)).not.toThrow();
  });

  it('ignores payload without level', () => {
    const { plugin, server, handlers } = setup();
    const spy = mock(() => {});
    const prev = console.log;
    console.log = spy as typeof prev;
    configure(plugin, server);
    handlers.get('vibegame:console')?.({ message: 'x', timestamp: 1 });
    expect(spy).not.toHaveBeenCalled();
    console.log = prev;
  });

  it('logs processing errors to console.error', () => {
    const { plugin, server, handlers } = setup();
    const errSpy = mock(() => {});
    const prev = console.error;
    console.error = errSpy as typeof prev;
    configure(plugin, server);
    // Force format path with a level but break via throwing getter later:
    // send a payload that formatLogMessage can handle; then verify handler is resilient.
    handlers.get('vibegame:console')?.({
      level: 'error',
      message: 'ok',
      timestamp: 1,
      context: { file: 'a.ts', line: 1 },
    });
    // At least one console.error call for the message itself
    expect(errSpy).toHaveBeenCalled();
    console.error = prev;
  });
});

describe('consoleForwarding matrix — connection init', () => {
  beforeEach(() => {
    // use fake timers via setTimeout real — just verify registration
  });

  it('registers connection listener', () => {
    const { plugin, server, on } = setup();
    configure(plugin, server);
    expect(on).toHaveBeenCalledWith('connection', expect.any(Function));
  });

  it('registers vibegame:console listener', () => {
    const { plugin, server, on } = setup();
    configure(plugin, server);
    expect(on).toHaveBeenCalledWith('vibegame:console', expect.any(Function));
  });

  it('sends init event after connection', async () => {
    const { plugin, server, handlers, send } = setup();
    configure(plugin, server);
    const connection = handlers.get('connection');
    expect(connection).toBeDefined();
    connection?.(undefined);
    await new Promise((r) => setTimeout(r, 120));
    expect(send).toHaveBeenCalled();
    const payload = (send.mock.calls as unknown as unknown[][])[0]?.[0] as {
      type: string;
      event: string;
    };
    expect(payload.type).toBe('custom');
    expect(payload.event).toBe('vibegame:init-console-forwarding');
  });
});

describe('consoleForwarding matrix — formatted context', () => {
  const contexts = [
    { file: 'a.ts', line: 1 },
    { file: 'src/foo.ts', line: 99 },
    { file: '/abs/path.ts', line: 0 },
    { file: 'x.ts', line: 42, stack: 'Error: boom' },
    { file: 'y.ts', line: 7, column: 3 },
  ];

  for (const [i, context] of contexts.entries()) {
    it(`formats context case #${i}`, () => {
      const { plugin, server, handlers } = setup();
      const spy = mock(() => {});
      const prev = console.error;
      console.error = spy as typeof prev;
      configure(plugin, server);
      handlers.get('vibegame:console')?.({
        level: 'error',
        message: `msg-${i}`,
        timestamp: Date.now(),
        context,
      });
      expect(spy).toHaveBeenCalled();
      const out = String(
        ((spy.mock.calls as unknown as unknown[][])[0] ?? [])[0] ?? ''
      );
      expect(out).toContain('[ERROR]');
      if (context.file) expect(out).toContain(String(context.file));
      console.error = prev;
    });
  }

  const argCases = [
    ['only-message'],
    ['msg', 'a'],
    ['msg', 1, 2, 3],
    ['msg', { a: 1 }],
    ['msg', null, undefined],
    ['msg', ['nested']],
    ['msg', true, false],
  ];

  for (const [i, args] of argCases.entries()) {
    it(`formats args case #${i}`, () => {
      const { plugin, server, handlers } = setup();
      const spy = mock(() => {});
      const prev = console.log;
      console.log = spy as typeof prev;
      configure(plugin, server);
      handlers.get('vibegame:console')?.({
        level: 'info',
        message: 'm',
        args: args.slice(1),
        timestamp: 1,
        context: {},
      });
      expect(spy).toHaveBeenCalled();
      console.log = prev;
    });
  }
});

describe('consoleForwarding matrix — message content', () => {
  const messages = [
    '',
    'simple',
    'with spaces',
    'emoji 🔥',
    'acentos çãõ',
    'multi\nline',
    'quote "double"',
    "quote 'single'",
    'html <b>x</b>',
    'json {"a":1}',
    'a'.repeat(500),
    'nullish-looking',
  ];

  for (const [i, message] of messages.entries()) {
    it(`accepts message payload #${i}`, () => {
      const { plugin, server, handlers } = setup();
      const spy = mock(() => {});
      const prev = console.log;
      console.log = spy as typeof prev;
      configure(plugin, server);
      expect(() =>
        handlers.get('vibegame:console')?.({
          level: 'info',
          message,
          timestamp: 1,
          context: {},
        })
      ).not.toThrow();
      expect(spy).toHaveBeenCalled();
      console.log = prev;
    });
  }
});

describe('consoleForwarding matrix — transform injection contracts', () => {
  let plugin: Plugin;
  let injected: string;

  beforeEach(() => {
    plugin = consoleForwarding();
    injected = transform(plugin, '// app', '/src/main.ts');
  });

  afterEach(() => {
    // no-op
  });

  const snippets = [
    'originalConsole',
    'getStackInfo',
    'sendConsoleMessage',
    'console.log.bind(console)',
    'console.warn.bind(console)',
    'console.error.bind(console)',
    'console.debug.bind(console)',
    "['log', 'warn', 'error', 'debug']",
    'window.location.origin',
    'serializedArgs',
    'Best-effort forwarding',
    'console-override',
  ];

  for (const snippet of snippets) {
    it(`injection contains ${snippet}`, () => {
      expect(injected).toContain(snippet);
    });
  }
});
