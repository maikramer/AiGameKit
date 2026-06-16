import { test, expect } from './fixtures/vibegame-fixtures';
import { heroEid, pressKey, waitForHeroReady } from './helpers/interaction';
import type { GameInspector } from './helpers/game-inspector';

async function heroPlanarPos(
  inspector: GameInspector,
  eid: number
): Promise<{ x: number; z: number }> {
  const t = await inspector.component(eid, 'transform');
  expect(t).not.toBeNull();
  return { x: t!.posX, z: t!.posZ };
}

test.describe('pause menu (Q) gates gameplay input', () => {
  test('movement is frozen while the menu is open, and resumes after', async ({
    vibegamePage,
    gameInspector,
  }) => {
    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);

    // Open the pause menu (Q toggles it).
    await pressKey(vibegamePage, 'KeyQ', 120);
    await vibegamePage.waitForTimeout(300);

    const before = await heroPlanarPos(gameInspector, eid);
    await pressKey(vibegamePage, 'KeyW', 700);
    await vibegamePage.waitForTimeout(150);
    const afterPaused = await heroPlanarPos(gameInspector, eid);

    const movedWhilePaused = Math.hypot(
      afterPaused.x - before.x,
      afterPaused.z - before.z
    );
    expect(
      movedWhilePaused,
      `hero must not move while paused (moved ${movedWhilePaused.toFixed(2)}m)`
    ).toBeLessThan(0.25);

    // Close the menu and confirm control returns.
    await pressKey(vibegamePage, 'KeyQ', 120);
    await vibegamePage.waitForTimeout(300);

    const resumeStart = await heroPlanarPos(gameInspector, eid);
    await pressKey(vibegamePage, 'KeyW', 700);
    await vibegamePage.waitForTimeout(150);
    const resumeEnd = await heroPlanarPos(gameInspector, eid);

    const movedAfterResume = Math.hypot(
      resumeEnd.x - resumeStart.x,
      resumeEnd.z - resumeStart.z
    );
    expect(
      movedAfterResume,
      `hero should move again after unpausing (moved ${movedAfterResume.toFixed(2)}m)`
    ).toBeGreaterThan(0.5);
  });
});
