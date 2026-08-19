import { expect, test } from '@playwright/test';

const HUD_HARNESS_ORIGIN = 'http://127.0.0.1:30990';

test.use({ baseURL: HUD_HARNESS_ORIGIN });

test.describe('HudScreenLayer — screen-space DOM overlay', () => {
  test('a .vibe-hud-screen-layer div is mounted over the canvas with overlay CSS', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.locator('#game-canvas')).toBeVisible({
      timeout: 15000,
    });
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });

    const layer = page.locator('.vibe-hud-screen-layer').first();
    await expect(layer).toBeAttached();

    const styles = await layer.evaluate((el) => {
      const cs = window.getComputedStyle(el);
      return {
        position: cs.position,
        pointerEvents: cs.pointerEvents,
        zIndex: cs.zIndex,
        top: cs.top,
        left: cs.left,
      };
    });

    expect(styles.position).toBe('absolute');
    expect(styles.pointerEvents).toBe('none');
    expect(styles.top).toBe('0px');
    expect(styles.left).toBe('0px');
    expect(parseInt(styles.zIndex, 10)).toBeGreaterThanOrEqual(10);

    await page.screenshot({
      path: '../../.sisyphus/evidence/task-20-hud-layer-found.png',
    });
  });

  test('the layer is a direct child of document.body (sibling of the canvas)', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });

    const parentTag = await page
      .locator('.vibe-hud-screen-layer')
      .first()
      .evaluate((el) => el.parentElement?.tagName ?? '');

    expect(parentTag).toBe('BODY');
  });

  test('only one .vibe-hud-screen-layer exists per page (singleton)', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });
    await page.waitForTimeout(500);

    const count = await page.locator('.vibe-hud-screen-layer').count();
    expect(count).toBe(1);
  });

  test('a widget registered via registerHudWidget mounts inside the layer', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForSelector('.hud-probe-widget', { timeout: 10000 });

    const inLayer = await page
      .locator('.hud-probe-widget')
      .evaluate((el) => el.closest('.vibe-hud-screen-layer') !== null);

    expect(inLayer).toBe(true);
  });
});
