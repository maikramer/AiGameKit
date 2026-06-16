import { expect, test } from '@playwright/test';

const FLOAT_HARNESS_ORIGIN = 'http://127.0.0.1:30989';

test.use({ baseURL: FLOAT_HARNESS_ORIGIN });

test.describe('FloatingText — screen-space DOM mode', () => {
  test('spawnFloatingText(space:"screen") mounts a .vibe-float-screen span', async ({
    page,
  }) => {
    page.on('console', (msg) => console.log('[browser]', msg.text()));
    page.on('pageerror', (err) => console.log('[pageerror]', err.message));
    await page.goto('/');
    await expect(page.locator('#game-canvas')).toBeVisible({ timeout: 15000 });
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });

    await page.locator('#spawn-screen').click();
    await page.waitForTimeout(120);

    const visible = await page
      .locator('.vibe-float-screen')
      .evaluateAll((els) =>
        els.filter((el) => (el as HTMLElement).style.opacity !== '0').length
      );
    expect(visible).toBeGreaterThanOrEqual(1);

    const layerParent = await page
      .locator('.vibe-float-screen')
      .first()
      .evaluate((el) => el.closest('.vibe-hud-screen-layer') !== null);
    expect(layerParent).toBe(true);

    await page.screenshot({
      path: '../.sisyphus/evidence/task-25-float-screen.png',
    });
  });

  test('crit mode paints the span red-orange (#ff6b3d / rgb(255,107,61))', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });
    await page.locator('#spawn-crit').click();
    await page.waitForTimeout(120);

    const critColor = await page
      .locator('.vibe-float-screen')
      .first()
      .evaluate((el) => (el as HTMLElement).style.color);
    expect(['#ff6b3d', 'rgb(255, 107, 61)']).toContain(critColor);

    await page.screenshot({
      path: '../.sisyphus/evidence/task-25-float-crit.png',
    });
  });
});

test.describe('FloatingText — world-mode regression (troika 3D)', () => {
  test('spawnFloatingText(space:"world") still works (button click, no crash)', async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (err) => errors.push(String(err)));
    await page.goto('/');
    await expect(page.locator('#game-canvas')).toBeVisible({ timeout: 15000 });
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });

    await page.locator('#spawn-world').click();
    await page.waitForTimeout(200);

    expect(
      errors.some((e) => e.includes('Text') || e.includes('troika'))
    ).toBe(false);

    await page.screenshot({
      path: '../.sisyphus/evidence/task-25-float-world-regression.png',
    });
  });
});
