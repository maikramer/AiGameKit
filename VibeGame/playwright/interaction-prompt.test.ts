import { expect, test } from '@playwright/test';

const HUD_HARNESS_ORIGIN = 'http://127.0.0.1:30990';

test.use({ baseURL: HUD_HARNESS_ORIGIN });

interface PromptHarness {
  player: number;
  merchant: number;
  setPlayerPos(x: number, z: number): void;
}

async function movePlayer(
  page: import('@playwright/test').Page,
  x: number,
  z: number
): Promise<void> {
  await page.evaluate(
    ([hx, hz]) => {
      const h = (window as unknown as { __promptHarness: PromptHarness })
        .__promptHarness;
      h.setPlayerPos(hx, hz);
    },
    [x, z]
  );
}

test.describe('InteractionPrompt — nearest-in-range gating', () => {
  test('shows the prompt with key + label when the player is in range, hides when out of range', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForSelector('.hud-prompt', { timeout: 10000 });

    const prompt = page.locator('.hud-prompt').first();

    await movePlayer(page, 3.0, 0);
    await expect(prompt).toBeVisible({ timeout: 5000 });
    await expect(prompt.locator('.hud-prompt-key')).toHaveText('K');
    await expect(prompt.locator('.hud-prompt-label')).toHaveText(
      'Talk to Merchant'
    );

    await page.screenshot({
      path: '../../.sisyphus/evidence/task-24-prompt-range.png',
    });

    await movePlayer(page, 5.0, 0);
    await expect(prompt).toBeHidden({ timeout: 5000 });
  });
});
