import { expect, test } from '@playwright/test';

test.describe('simple-rpg smoke', () => {
  test('carrega a página com canvas', async ({ page }) => {
    await page.goto('/');
    await expect(page).toHaveTitle(/Simple RPG Demo/i);
    await expect(page.locator('#game-canvas')).toBeVisible();
  });

  test('WebGL: sem falha de shader do terreno (instanceUVTransform / instanceEdgeSkirt)', async ({
    page,
  }) => {
    const consoleLines: string[] = [];

    page.on('console', (msg) => {
      const t = msg.text();
      consoleLines.push(`[${msg.type()}] ${t}`);
    });

    await page.goto('/');
    await expect(page.locator('#game-canvas')).toBeVisible();
    await page.waitForTimeout(3500);

    const joined = consoleLines.join('\n');

    expect(
      joined,
      'vertex shader do terreno deve declarar instanceUVTransform'
    ).not.toMatch(/instanceUVTransform.*undeclared identifier/i);
    expect(
      joined,
      'vertex shader do terreno deve declarar instanceEdgeSkirt'
    ).not.toMatch(/instanceEdgeSkirt.*undeclared identifier/i);
  });
});
