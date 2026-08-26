import { expect, test } from '@playwright/test';

/**
 * simple-farm smoke — boot invariants + the farming loop driven through the
 * __VIBEGAME__ debug bridge (deterministic, no synthetic keyboard timing).
 * Runs against its own webServer (port 30987, see playwright.config.ts).
 *
 * Bridge methods never cross the evaluate boundary (functions are stripped by
 * structured clone) — every interaction runs inside page.evaluate.
 */

const FARM_ORIGIN = 'http://127.0.0.1:30987';

test.use({ baseURL: FARM_ORIGIN });

async function waitForBridge(
  page: import('@playwright/test').Page
): Promise<void> {
  await page.waitForFunction(
    () => !!(window as unknown as Record<string, unknown>).__VIBEGAME__,
    undefined,
    { timeout: 90_000 }
  );
}

function evaluateFarm<T>(
  page: import('@playwright/test').Page,
  fn: string
): Promise<T> {
  return page.evaluate(
    `(function(){ const b = window.__VIBEGAME__; ${fn} })()`
  ) as Promise<T>;
}

test.describe('simple-farm smoke', () => {
  test('boot: canvas, HUD widgets, single world instances', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Simple Farm/i);
    await expect(page.locator('#game-canvas')).toBeVisible();

    await waitForBridge(page);
    await page.waitForTimeout(4000);

    const counts = await evaluateFarm<Record<string, unknown>>(
      page,
      `
      return {
        grids: b.query('farm-grid').length,
        clocks: b.query('game-clock').length,
        players: b.query('player-controller').length,
        home: b.state.getEntityByName('farm_home'),
        market: b.state.getEntityByName('farm_market'),
      };
    `
    );

    expect(counts.grids).toBe(1);
    expect(counts.clocks).toBe(1);
    expect(counts.players).toBe(1);
    expect(counts.home).not.toBeNull();
    expect(counts.market).not.toBeNull();

    await expect(page.locator('.hud-hotbar-card')).toHaveCount(6);
    await expect(page.locator('.hud-statbar-text')).toHaveText(
      /100\s*\/\s*100/
    );
  });

  test('farm debug var reflects the boot state', async ({ page }) => {
    await page.goto('/');
    await waitForBridge(page);
    // initFarmGame runs after runtime.start() — poll until the var exists.
    await page.waitForFunction(
      () => {
        const b = (
          window as unknown as {
            __VIBEGAME__?: { debug: { getVar: (n: string) => unknown } };
          }
        ).__VIBEGAME__;
        return b?.debug?.getVar('farm') !== undefined;
      },
      undefined,
      { timeout: 60_000 }
    );

    const farm = await evaluateFarm<{
      day: number;
      gold: number;
      stamina: { cur: number; max: number };
      activeTool: string;
      tools: string[];
    }>(page, `return b.debug.getVar('farm');`);

    expect(farm.day).toBe(1);
    expect(farm.gold).toBe(500);
    // Live value (regen runs from frame one) — bounded, not exact.
    expect(farm.stamina.max).toBe(100);
    expect(farm.stamina.cur).toBeGreaterThan(90);
    expect(farm.stamina.cur).toBeLessThanOrEqual(100);
    expect(farm.activeTool).toBe('hoe');
    expect(farm.tools).toEqual([
      'hoe',
      'can',
      'turnip_seeds',
      'tomato_seeds',
      'potato_seeds',
      'hand',
    ]);
  });

  test('loop: till → sleep → day 2', async ({ page }) => {
    await page.goto('/');
    await waitForBridge(page);
    // Debug actions register after runtime.start() — poll until ready.
    await page.waitForFunction(
      () => {
        const b = (
          window as unknown as {
            __VIBEGAME__?: { debug: { getVar: (n: string) => unknown } };
          }
        ).__VIBEGAME__;
        return b?.debug?.getVar('farm') !== undefined;
      },
      undefined,
      { timeout: 60_000 }
    );

    // Stand on the field (plot origin (0,16), rows extend to +z). The world
    // may still be settling through its loading gate — poll until the facing
    // tile resolves on-grid.
    const tp = await evaluateFarm<number>(
      page,
      `
      return b.debug.callAction('tp', 0, 12.5, 12);
    `
    );
    expect(tp).toBeGreaterThan(0);

    await page.waitForFunction(
      () => {
        const b = (
          window as unknown as {
            __VIBEGAME__?: { debug: { callAction: (n: string) => unknown } };
          }
        ).__VIBEGAME__;
        const tile = b?.debug?.callAction('tile-facing');
        return typeof tile === 'object' && tile !== null;
      },
      undefined,
      { timeout: 60_000 }
    );

    // Fixed-cell determinism: the facing cell can drift while the player body
    // settles after the teleport, so till a chosen cell directly.
    const gridReady = await evaluateFarm<{ state: number } | null>(
      page,
      `
      return b.debug.callAction('tile-at', 5, 3);
    `
    );
    expect(gridReady).not.toBeNull();
    expect(gridReady.state).toBe(0); // Empty

    const tilled = await evaluateFarm<boolean>(
      page,
      `
      return b.debug.callAction('till-at', 5, 3);
    `
    );
    expect(tilled).toBe(true);

    const tile = await evaluateFarm<{ state: number }>(
      page,
      `
      return b.debug.callAction('tile-at', 5, 3);
    `
    );
    expect(tile.state).toBe(1); // Tilled

    await evaluateFarm(page, `b.debug.callAction('sleep');`);
    await page.waitForTimeout(600);

    const farm = await evaluateFarm<{ day: number; stamina: { cur: number } }>(
      page,
      `
      return b.debug.getVar('farm');
    `
    );
    expect(farm.day).toBe(2);
    expect(farm.stamina.cur).toBe(100);
  });
});
