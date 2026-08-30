import type { Plugin, ViteDevServer } from 'vite';
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  renameSync,
  statSync,
} from 'node:fs';
import { join } from 'node:path';
import { formatLogMessage, type LogMessage } from '../core/utils/logger.ts';

/**
 * Durable record of browser warn/error messages for AI-agent debugging: the
 * agent reads the file with its file tools even after the page crashed or
 * reloaded. Rotates at {@link MAX_LOG_BYTES} (`.log` → `.log.1`).
 */
const LOG_DIR = '.vibegame';
const MAX_LOG_BYTES = 512 * 1024;

export function consoleForwarding(): Plugin {
  let server: ViteDevServer;
  let logFile = '';

  function appendToLogFile(formatted: string, level: string): void {
    if (level !== 'warn' && level !== 'error') return;
    try {
      const dir = join(server.config.root, LOG_DIR);
      if (!logFile) logFile = join(dir, 'console.log');
      mkdirSync(dir, { recursive: true });
      if (existsSync(logFile) && statSync(logFile).size > MAX_LOG_BYTES) {
        renameSync(logFile, `${logFile}.1`);
      }
      appendFileSync(logFile, `${new Date().toISOString()} ${formatted}\n`);
    } catch {
      // Never break the dev server over log IO.
    }
  }

  return {
    name: 'vibegame:console-forwarding',
    apply: 'serve',
    enforce: 'post',

    configureServer(_server: ViteDevServer) {
      server = _server;

      server.ws.on('vibegame:console', (data: LogMessage) => {
        try {
          if (!data || !data.level) return;

          if (!data.timestamp || isNaN(data.timestamp)) {
            data.timestamp = Date.now();
          }

          if (!data.context) {
            data.context = {};
          }

          const formatted = formatLogMessage(data);
          appendToLogFile(formatted, data.level);

          switch (data.level) {
            case 'debug':
              console.debug(formatted);
              break;
            case 'info':
              console.log(formatted);
              break;
            case 'warn':
              console.warn(formatted);
              break;
            case 'error':
              console.error(formatted);
              break;
            default:
              console.log(formatted);
          }
        } catch (error) {
          console.error('[VibeGame] Error processing console message:', error);
        }
      });

      server.ws.on('connection', () => {
        setTimeout(() => {
          server.ws.send({
            type: 'custom',
            event: 'vibegame:init-console-forwarding',
          });
        }, 100);
      });
    },

    transform(code: string, id: string) {
      if (id.includes('/src/main.ts') || id.includes('/src/main.js')) {
        const injection = `
if (import.meta.hot) {
  const originalConsole = {
    log: console.log.bind(console),
    warn: console.warn.bind(console),
    error: console.error.bind(console),
    debug: console.debug.bind(console),
  };

  // Vite 8's HMR client (module-runner transport) throws SendBeforeConnectError
  // when import.meta.hot.send runs before the socket connects, and the client
  // logs that rejection via console.error — which this override would re-forward
  // and re-send, cascading until the socket opens. Always attempt the send and
  // queue only on failure: the connect event routinely fires before main.ts is
  // evaluated, so gating on a wsConnected flag left every post-boot message
  // stuck in the queue forever (the self-heal window covered only the first
  // seconds). SendBeforeConnectError itself is never surfaced or forwarded.
  const MAX_QUEUED = 200;
  let flushTimer = 0;
  const pending = [];

  function scheduleFlush() {
    if (flushTimer !== 0 || pending.length === 0) return;
    flushTimer = setTimeout(flushPending, 0);
  }

  function flushPending() {
    flushTimer = 0;
    const batch = pending.splice(0);
    for (const message of batch) {
      try {
        import.meta.hot.send('vibegame:console', message);
      } catch (e) {
        // Socket closed mid-flush: stop; the next send attempt re-queues.
        pending.unshift(message);
        return;
      }
    }
  }

  import.meta.hot.on('vite:ws:connect', () => {
    scheduleFlush();
  });
  import.meta.hot.on('vite:ws:disconnect', () => {
    scheduleFlush();
  });

  function getStackInfo() {
    const stack = new Error().stack;
    if (!stack) return {};
    
    const lines = stack.split('\\n');
    for (let i = 2; i < lines.length; i++) {
      const line = lines[i];
      if (line.includes('console-override')) continue;
      
      const match = line.match(/at\\s+.*?\\s+\\(?(.*?):(\\d+):(\\d+)\\)?/);
      if (match) {
        return {
          file: match[1].replace(window.location.origin, ''),
          line: parseInt(match[2], 10),
        };
      }
    }
    return {};
  }
  
  function sendConsoleMessage(level, args) {
    const stackInfo = getStackInfo();
    const serializedArgs = args.length > 1
      ? Array.from(args).slice(1).map(a => a instanceof Error ? a.stack || a.message : a)
      : undefined;
    const message = {
      level,
      message: args[0] instanceof Error ? args[0].message : (args[0]?.toString() || ''),
      args: serializedArgs,
      timestamp: Date.now(),
      context: {
        ...stackInfo,
      },
    };
    
    if (args[0] instanceof Error) {
      message.context.stack = args[0].stack;
    }
    
    try {
      import.meta.hot.send('vibegame:console', message);
    } catch (e) {
      // Not connected yet (or mid-reconnect): queue for the next flush. Never
      // throw out of the console override — an uncaught throw here re-enters
      // console.error via the browser's uncaught-error path and loops.
      if (pending.length < MAX_QUEUED) {
        pending.push(message);
      }
    }
  }
  
  ['log', 'warn', 'error', 'debug'].forEach(method => {
    console[method] = function(...args) {
      // Suppress known Rapier initialization warning
      if (method === 'warn' && args[0]?.toString().includes('using deprecated parameters for the initialization function')) {
        return;
      }
      // Transient HMR-socket race (Vite 8 module-runner): never show or forward
      // it — the client reports send failures through console.error, which would
      // loop back into this override while the socket reconnects after a reload.
      if (args[0] instanceof Error && args[0].name === 'SendBeforeConnectError') {
        return;
      }
      originalConsole[method](...args);
      sendConsoleMessage(method === 'log' ? 'info' : method, args);
    };
  });
  
  console.log('[VibeGame] Console forwarding enabled');
}
`;
        return injection + '\n' + code;
      }
      return code;
    },
  };
}
