import { spawnSync } from 'node:child_process';
import { defineConfig, devices } from '@playwright/test';

/** Porta só para o webServer do Playwright (evita colisão com `npm run dev` em 3011). */
const PLAYWRIGHT_DEV_PORT = 30991;
const PLAYWRIGHT_ORIGIN = `http://127.0.0.1:${PLAYWRIGHT_DEV_PORT}`;

/**
 * Modo CDP (browser já em execução com depuração remota):
 * - `PLAYWRIGHT_CDP_WS`: WebSocket completo (`webSocketDebuggerUrl` de /json/version), ou
 * - `PLAYWRIGHT_CDP_URL`: base HTTP (ex.: `http://127.0.0.1:9222`); o config obtém o JSON
 *   via `http`/`https` nativos do Node (sem `curl` no PATH).
 * Inicie o Chrome/Edge com `--remote-debugging-port=9222`.
 * Com CDP ativo, `webServer` fica desligado: suba o Vite do exemplo à mão se precisar da app.
 * Nesse caso use `PLAYWRIGHT_BASE_URL` se não for `http://127.0.0.1:3011`.
 */
function httpGetBodySync(fullUrl: string): string {
  const embedded = JSON.stringify(fullUrl);
  const script = `
const http = require('http');
const https = require('https');
const { URL } = require('url');
const fullUrl = ${embedded};
const u = new URL(fullUrl);
const lib = u.protocol === 'https:' ? https : http;
lib.get(fullUrl, (res) => {
  const chunks = [];
  res.on('data', (c) => chunks.push(c));
  res.on('end', () => process.stdout.write(Buffer.concat(chunks)));
}).on('error', (e) => { process.stderr.write(String(e.message)); process.exit(1); });
`;
  const r = spawnSync(process.execPath, ['-e', script], {
    encoding: 'buffer',
    maxBuffer: 1024 * 1024,
    windowsHide: true,
  });
  if (r.error) throw r.error;
  if (r.status !== 0) {
    throw new Error(r.stderr?.toString('utf8') || 'http get failed');
  }
  return r.stdout.toString('utf8');
}

function resolveCdpWs(): string | undefined {
  if (process.env.PLAYWRIGHT_CDP_WS) {
    return process.env.PLAYWRIGHT_CDP_WS;
  }
  const httpUrl = process.env.PLAYWRIGHT_CDP_URL;
  if (!httpUrl) {
    return undefined;
  }
  const base = httpUrl.replace(/\/$/, '');
  try {
    const out = httpGetBodySync(`${base}/json/version`);
    const json = JSON.parse(out) as { webSocketDebuggerUrl?: string };
    if (!json.webSocketDebuggerUrl) {
      throw new Error('resposta sem webSocketDebuggerUrl');
    }
    return json.webSocketDebuggerUrl;
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    throw new Error(
      `PLAYWRIGHT_CDP_URL: não foi possível obter WebSocket de ${base}/json/version (${msg})`
    );
  }
}

const cdpWs = resolveCdpWs();
const useCdp = Boolean(cdpWs);
const cdpUse = useCdp
  ? { connectOptions: { wsEndpoint: cdpWs as string } }
  : {};

const baseURL = useCdp
  ? (process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:3011')
  : PLAYWRIGHT_ORIGIN;

export default defineConfig({
  testDir: 'tests/playwright',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    ...cdpUse,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: useCdp
    ? undefined
    : {
        command: `BROWSER=none npx vite dev --host 127.0.0.1 --port ${PLAYWRIGHT_DEV_PORT} --strictPort`,
        cwd: 'examples/simple-rpg',
        url: PLAYWRIGHT_ORIGIN,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
