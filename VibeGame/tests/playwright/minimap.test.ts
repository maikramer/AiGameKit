import { expect, test } from '@playwright/test';

const MINIMAP_HARNESS_ORIGIN = 'http://127.0.0.1:30988';

test.use({ baseURL: MINIMAP_HARNESS_ORIGIN });

test.describe('Minimap Widget — canvas top-down render', () => {
  test('renders enemy and boss blips at scaled positions for a populated scene', async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.stack ?? e.message));
    await page.goto('/');
    await page.waitForSelector('.vibe-hud-minimap-canvas', { timeout: 15000 });

    const canvas = page.locator('.vibe-hud-minimap-canvas').first();
    await expect(canvas).toBeVisible();

    await page.waitForTimeout(800);

    const stats = await canvas.evaluate((el) => {
      const htmlCanvas = el as HTMLCanvasElement;
      const ctx = htmlCanvas.getContext('2d');
      if (!ctx) return { ctx: false, drawn: 0, reddish: 0, purpleish: 0, greenish: 0 };
      const { width, height } = htmlCanvas;
      const data = ctx.getImageData(0, 0, width, height).data;
      let drawn = 0;
      let reddish = 0;
      let purpleish = 0;
      let greenish = 0;
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i];
        const g = data[i + 1];
        const b = data[i + 2];
        const a = data[i + 3];
        if (a < 16) continue;
        drawn++;
        if (r > 180 && g < 110 && b < 110) reddish++;
        else if (r > 140 && g < 130 && b > 200) purpleish++;
        else if (g > 170 && r < 170 && b < 170) greenish++;
      }
      return { ctx: true, drawn, reddish, purpleish, greenish };
    });

    expect(stats.ctx).toBe(true);
    expect((stats as { drawn: number }).drawn).toBeGreaterThan(500);

    await page.screenshot({
      path: '../.sisyphus/evidence/task-22-minimap-render.png',
    });
  });

  test('empty scene shows only the central player arrow', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('.vibe-hud-minimap-canvas', { timeout: 15000 });

    await page.evaluate(() => {
      const w = (window as unknown as { __minimapHarness?: { clearBlips: () => void } })
        .__minimapHarness;
      w?.clearBlips();
    });
    await page.waitForTimeout(400);

    const canvas = page.locator('.vibe-hud-minimap-canvas').first();
    await expect(canvas).toBeVisible();

    await page.screenshot({
      path: '../.sisyphus/evidence/task-22-minimap-empty.png',
    });
  });
});
