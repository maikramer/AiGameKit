import { test } from '@playwright/test';
import { GameInspector } from './helpers/game-inspector';

test.setTimeout(300_000);

test('diagnóstico: estado do player', async ({ page }) => {
  const consoleLines: string[] = [];
  page.on('console', (msg) => {
    const t = msg.text();
    if (/error|warn/i.test(msg.type()) || /hero|player|spawn-gate/i.test(t))
      consoleLines.push(`[${msg.type()}] ${t}`);
  });
  page.on('pageerror', (err) => {
    consoleLines.push(`[pageerror] ${err.message}`);
  });

  await page.goto('/');
  const inspector = new GameInspector(page);
  await inspector.waitForBridge(60_000);

  // Espera a tela de loading sumir (ou 150s em caso de lentidão headless)
  await page
    .waitForFunction(
      () => {
        const els = Array.from(document.querySelectorAll('body *'));
        return !els.some(
          (e) =>
            e.children.length === 0 &&
            /preparing the world|loading models/i.test(e.textContent ?? '')
        );
      },
      undefined,
      { timeout: 150_000 }
    )
    .catch(() => console.log('=== LOADING AINDA VISÍVEL após 150s'));

  await page.waitForTimeout(3000);

  const player = await inspector.entity('player');
  console.log('=== PLAYER ENTITY ===');
  console.log(JSON.stringify(player, null, 2));

  const sceneInfo = await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    type Bridge = {
      snapshot: () => string;
      entity: (n: string) => unknown;
      query: (...c: string[]) => number[];
    };
    const b = w.__VIBEGAME__ as Bridge | undefined;

    // câmera
    let cam: Record<string, unknown> | null = null;
    const canvases = document.querySelectorAll('canvas');
    for (const c of canvases) {
      const evt = (c as unknown as Record<string, unknown>)[
        '__vibegameCameraProbe'
      ];
      if (evt) cam = evt as Record<string, unknown>;
    }
    return {
      bridgeKeys: b ? Object.keys(b) : null,
      snapshotHead: b ? b.snapshot().slice(0, 600) : null,
    };
  });
  console.log('=== SNAPSHOT HEAD ===');
  console.log(JSON.stringify(sceneInfo, null, 2));

  await page.screenshot({ path: 'test-results/diag-player2.png' });

  console.log('=== CONSOLE (filtrado) ===');
  const seen = new Set<string>();
  for (const l of consoleLines) {
    if (seen.has(l)) continue;
    seen.add(l);
    console.log(l);
  }
});
