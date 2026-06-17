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
    await page.waitForTimeout(2000);
    await use(page);
  },
  gameInspector: async ({ vibegamePage }, use) => {
    const inspector = new GameInspector(vibegamePage);
    await use(inspector);
  },
});
export { expect };
