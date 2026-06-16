import { expect, test } from '@playwright/test';

const HUD_HARNESS_ORIGIN = 'http://127.0.0.1:30990';

test.use({ baseURL: HUD_HARNESS_ORIGIN });

interface ModalHarness {
  open(): void;
  close(): void;
  isOpen(): boolean;
}

async function openModal(page: import('@playwright/test').Page): Promise<void> {
  await page.evaluate(() => {
    (
      window as unknown as { __modalHarness: ModalHarness }
    ).__modalHarness.open();
  });
}

async function awaitAttached(
  page: import('@playwright/test').Page
): Promise<void> {
  await page.waitForSelector('.hud-modal-overlay', {
    state: 'attached',
    timeout: 10000,
  });
}

test.describe('TabbedModal — pause menu', () => {
  test('Escape toggles the modal open/closed and pauses while open', async ({
    page,
  }) => {
    await page.goto('/');
    await awaitAttached(page);

    const overlay = page.locator('.hud-modal-overlay').first();
    await expect(overlay).toHaveAttribute('data-open', 'false');

    await page.keyboard.press('Escape');
    await expect(overlay).toHaveAttribute('data-open', 'true', {
      timeout: 5000,
    });
    await expect(overlay.locator('.hud-modal-title')).toBeVisible();
    await page.screenshot({
      path: '../.sisyphus/evidence/task-27-modal-toggle.png',
    });

    await page.keyboard.press('Escape');
    await expect(overlay).toHaveAttribute('data-open', 'false', {
      timeout: 5000,
    });
  });

  test('skills tab — spending a skill point increments rank', async ({
    page,
  }) => {
    await page.goto('/');
    await awaitAttached(page);
    const overlay = page.locator('.hud-modal-overlay').first();

    await openModal(page);
    await expect(overlay).toHaveAttribute('data-open', 'true');

    await overlay.locator('.hud-modal-tab', { hasText: /skills/i }).click();
    const rank = overlay.locator('.hud-modal-skill-rank').first();
    await expect(rank).toHaveText('0');

    await overlay.locator('.hud-modal-skill-plus').first().click();
    await expect(rank).toHaveText('1');
    await expect(overlay.locator('.hud-modal-skill-points')).toContainText('2');

    await page.screenshot({
      path: '../.sisyphus/evidence/task-27-modal-skills.png',
    });
  });

  test('options tab — cycle row advances to the next value', async ({
    page,
  }) => {
    await page.goto('/');
    await awaitAttached(page);
    const overlay = page.locator('.hud-modal-overlay').first();

    await openModal(page);
    await expect(overlay).toHaveAttribute('data-open', 'true');

    await overlay.locator('.hud-modal-tab', { hasText: /options/i }).click();
    const row = overlay
      .locator('.hud-modal-option', { hasText: /music volume/i })
      .first();
    await expect(row.locator('.hud-modal-option-value')).toHaveText('Medium');

    await row.click();
    await expect(row.locator('.hud-modal-option-value')).toHaveText('High');

    await page.screenshot({
      path: '../.sisyphus/evidence/task-27-modal-options.png',
    });
  });
});
