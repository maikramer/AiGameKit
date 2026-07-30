import { test as base, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import {
  GameInspector,
  injectWebGLErrorCapture,
  installConsoleCapture,
} from '../helpers/game-inspector';

interface VibeGameFixtures {
  vibegamePage: Page;
  gameInspector: GameInspector;
}
export const test = base.extend<VibeGameFixtures>({
  vibegamePage: async ({ page }, use) => {
    installConsoleCapture(page);
    await page.goto('/');
    await expect(page.locator('#game-canvas')).toBeVisible({ timeout: 30000 });
    await injectWebGLErrorCapture(page);
    const inspector = new GameInspector(page);
    // The debug bridge only installs once the loading gate latches (terrain +
    // spawn + assets ready). On a cold dev server that can exceed 15s, so allow
    // generous headroom to keep the simple-rpg suite from flaking on startup.
    await inspector.waitForBridge(45000);
    // The input plugin only routes keyboard events once the canvas has been
    // focused (`focusin` → `focusedCanvas`), so a page that was never clicked
    // has a dead keyboard. Focus it directly instead of `locator.click()`:
    // a click waits for actionability, and while the loading overlay is still
    // up it retries until the test times out — turning a slow boot into a
    // suite-wide setup failure.
    // Real gesture first (unlocks the audio context); it hits whatever is
    // topmost and can never block — but it also moves focus, so the canvas
    // focus below has to come after it.
    await page.mouse.click(5, 5);
    await page.evaluate(() => {
      const canvas = document.querySelector(
        '#game-canvas'
      ) as HTMLCanvasElement | null;
      if (!canvas) return;
      if (canvas.tabIndex < 0) canvas.tabIndex = 0;
      canvas.focus();
    });
    await page.waitForTimeout(2000);
    await use(page);
  },
  gameInspector: async ({ vibegamePage }, use) => {
    const inspector = new GameInspector(vibegamePage);
    await use(inspector);
  },
});
export { expect };
