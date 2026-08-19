import { test, expect } from './fixtures/vibegame-fixtures';

// Benign console noise the smoke tests already tolerate.
const BENIGN = [/deprecated/i, /REGL/i, /rapier/i, /chrome-extension/i];

test.describe('rendering: viewport resize stability', () => {
  test('resizing through several sizes raises no GL/console errors', async ({
    vibegamePage,
    gameInspector,
  }) => {
    const sizes = [
      { width: 800, height: 600 },
      { width: 1280, height: 720 },
      { width: 1024, height: 768 },
      { width: 1440, height: 900 },
      { width: 640, height: 480 },
    ];

    for (const size of sizes) {
      await vibegamePage.setViewportSize(size);
      await vibegamePage.waitForTimeout(350);
    }

    const glErrors = await gameInspector.captureWebGLErrors();
    expect(
      glErrors,
      `resize should not trigger shader/program errors. Got: ${JSON.stringify(glErrors)}`
    ).toEqual([]);

    const consoleErrors = (await gameInspector.captureConsoleErrors()).filter(
      (e) => !BENIGN.some((p) => p.test(e))
    );
    expect(
      consoleErrors,
      `resize should not log errors. Got: ${JSON.stringify(consoleErrors)}`
    ).toEqual([]);
  });

  test('canvas tracks the final viewport size', async ({ vibegamePage }) => {
    await vibegamePage.setViewportSize({ width: 1100, height: 700 });
    await vibegamePage.waitForTimeout(400);

    const box = await vibegamePage.locator('#game-canvas').boundingBox();
    expect(box).not.toBeNull();
    // Canvas should fill the viewport (full-bleed game canvas), not collapse.
    expect(box!.width).toBeGreaterThan(900);
    expect(box!.height).toBeGreaterThan(500);
  });
});
