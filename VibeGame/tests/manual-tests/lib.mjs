// Shared helpers for manual/automated browser inspection of the simple-rpg example.
// These drive a real Chromium via Playwright against a running Vite dev server.
//
// Start the server first (from repo root):
//   (cd examples/simple-rpg && BROWSER=none npx vite dev --host 127.0.0.1 --port 30991 --strictPort)
// then run any check, e.g.:
//   node tests/manual-tests/check-movement.mjs
//
// Env overrides: VIBE_URL (default http://127.0.0.1:30991/), VIBE_BOOT_MS (default 8000),
// VIBE_HEADLESS=0 to watch the browser.

import { chromium } from '../../node_modules/playwright/index.mjs';

export const URL = process.env.VIBE_URL || 'http://127.0.0.1:30991/';
export const BOOT_MS = Number(process.env.VIBE_BOOT_MS || 8000);
const HEADLESS = process.env.VIBE_HEADLESS !== '0';

/**
 * Launch the game, wait for boot, run `fn(page, ctx)`, always close the browser.
 * `ctx.logs` collects console + pageerror entries for assertions.
 */
export async function withGame(fn) {
  const browser = await chromium.launch({ headless: HEADLESS });
  const page = await browser.newPage();
  const logs = [];
  page.on('console', (m) => logs.push({ type: m.type(), text: m.text() }));
  page.on('pageerror', (e) =>
    logs.push({ type: 'pageerror', text: e.message })
  );
  await page.goto(URL, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(BOOT_MS);
  try {
    return await fn(page, { logs, browser });
  } finally {
    await browser.close();
  }
}

/** The example exposes window.__heroDebug() with live hero physics/animation fields. */
export async function heroDebug(page) {
  return page.evaluate(() =>
    window.__heroDebug ? window.__heroDebug() : null
  );
}

/** Keyboard input needs canvas focus first. */
export async function focusCanvas(page) {
  await page
    .click('#game-canvas', { position: { x: 640, y: 360 } })
    .catch(() => {});
}

export async function holdKey(page, key, ms) {
  await page.keyboard.down(key);
  await page.waitForTimeout(ms);
  await page.keyboard.up(key);
}

/** Sample a projector function over time while a key is held. Returns rows. */
export async function sampleWhileHolding(
  page,
  key,
  { samples = 25, intervalMs = 80, project }
) {
  await focusCanvas(page);
  await page.keyboard.down(key);
  const rows = [];
  for (let i = 0; i < samples; i++) {
    rows.push(await page.evaluate(project));
    await page.waitForTimeout(intervalMs);
  }
  await page.keyboard.up(key);
  return rows;
}

export const errorsFrom = (logs) =>
  logs.filter((l) => l.type === 'error' || l.type === 'pageerror');

/** tiny green/red console reporter */
export function report(name, ok, detail = '') {
  const tag = ok ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m';
  console.log(`${tag}  ${name}${detail ? ' — ' + detail : ''}`);
  return ok;
}
