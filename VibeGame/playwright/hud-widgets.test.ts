import { expect, test } from '@playwright/test';

const HUD_HARNESS_ORIGIN = 'http://127.0.0.1:30990';

test.use({ baseURL: HUD_HARNESS_ORIGIN });

test.describe('HUD core widgets (T21)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('.vibe-hud-screen-layer', { timeout: 10000 });
    await page.waitForSelector('.hud-health', { timeout: 10000 });
  });

  test('HealthBar mounts and binds Health.current/max', async ({ page }) => {
    const health = page.locator('.hud-health').first();
    await expect(health).toBeAttached();
    await expect(health.locator('.hud-health-text')).toHaveText('100/100');

    await page.evaluate(() => (window as any).__hudWidgets.damage(30));
    await page.waitForTimeout(120);
    await expect(health.locator('.hud-health-text')).toHaveText('70/100');

    await page.screenshot({
      path: '../.sisyphus/evidence/task-21-hud-health.png',
    });
  });

  test('ResourceChip reacts to addResource', async ({ page }) => {
    const gold = page.locator('.hud-resource-gold').first();
    await expect(gold).toBeAttached();
    await expect(gold.locator('.hud-resource-value')).toHaveText('0');

    await page.evaluate(() => (window as any).__hudWidgets.addGold(50));
    await page.waitForTimeout(120);
    await expect(gold.locator('.hud-resource-value')).toHaveText('50');

    await page.screenshot({
      path: '../.sisyphus/evidence/task-21-hud-resource.png',
    });
  });

  test('XpBar fills with addXp and resets on level-up', async ({ page }) => {
    const xp = page.locator('.hud-xp').first();
    await expect(xp).toBeAttached();
    await expect(xp.locator('.hud-xp-level')).toHaveText('1');

    await page.evaluate(() => (window as any).__hudWidgets.gainXp(3));
    await page.waitForTimeout(120);
    const fillWidth = await xp
      .locator('.hud-xp-fill')
      .evaluate((el) => (el as HTMLElement).style.width);
    expect(parseFloat(fillWidth)).toBeCloseTo(50, 0);

    await page.evaluate(() => (window as any).__hudWidgets.gainXp(3));
    await page.waitForTimeout(120);
    await expect(xp.locator('.hud-xp-level')).toHaveText('2');

    await page.screenshot({
      path: '../.sisyphus/evidence/task-21-hud-xp.png',
    });
  });

  test('BossBar appears only when the boss is within range', async ({
    page,
  }) => {
    const boss = page.locator('.hud-boss').first();
    await expect(boss).toBeAttached();
    await expect(boss).toBeHidden();

    await page.evaluate(() => (window as any).__hudWidgets.moveBoss(40, 0));
    await page.waitForTimeout(120);
    await expect(boss).toBeVisible();
    await expect(boss.locator('.hud-boss-text')).toContainText('200/200');

    await page.screenshot({
      path: '../.sisyphus/evidence/task-21-hud-boss-range.png',
    });
  });

  test('Timer, Mission and Controls widgets render', async ({ page }) => {
    await expect(page.locator('.hud-timer')).toBeAttached();
    await expect(page.locator('.hud-mission')).toBeAttached();
    await expect(page.locator('.hud-mission-title')).not.toBeEmpty();
    await expect(page.locator('.hud-controls')).not.toBeEmpty();
  });
});
