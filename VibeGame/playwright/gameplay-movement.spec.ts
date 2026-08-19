import { test, expect } from './fixtures/vibegame-fixtures';
import { heroEid, pressKey, waitForHeroReady } from './helpers/interaction';
import type { GameInspector } from './helpers/game-inspector';

async function heroTransform(
  inspector: GameInspector,
  eid: number
): Promise<Record<string, number>> {
  const t = await inspector.component(eid, 'transform');
  expect(t, 'hero should have a transform').not.toBeNull();
  return t!;
}

/** Angle (radians) between two unit quaternions, via |dot|. */
function quatAngle(
  a: Record<string, number>,
  b: Record<string, number>
): number {
  const dot =
    a.rotX * b.rotX + a.rotY * b.rotY + a.rotZ * b.rotZ + a.rotW * b.rotW;
  return Math.acos(Math.min(1, Math.abs(dot))) * 2;
}

test.describe('gameplay: player locomotion', () => {
  test('holding W drives the hero forward', async ({
    vibegamePage,
    gameInspector,
  }) => {
    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);

    const before = await heroTransform(gameInspector, eid);
    await pressKey(vibegamePage, 'KeyW', 900);
    await vibegamePage.waitForTimeout(150);
    const after = await heroTransform(gameInspector, eid);

    const dist = Math.hypot(after.posX - before.posX, after.posZ - before.posZ);
    expect(
      dist,
      `hero should travel a meaningful distance forward (moved ${dist.toFixed(2)}m)`
    ).toBeGreaterThan(0.5);
  });

  test('Space lifts the hero off the ground', async ({
    vibegamePage,
    gameInspector,
  }) => {
    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);

    const groundY = (await heroTransform(gameInspector, eid)).posY;

    await vibegamePage.keyboard.down('Space');
    let peakY = groundY;
    for (let i = 0; i < 12; i++) {
      await vibegamePage.waitForTimeout(50);
      const t = await heroTransform(gameInspector, eid);
      peakY = Math.max(peakY, t.posY);
    }
    await vibegamePage.keyboard.up('Space');

    expect(
      peakY - groundY,
      `hero should rise while jumping (peak +${(peakY - groundY).toFixed(2)}m)`
    ).toBeGreaterThan(0.2);

    // And come back down (within a couple seconds) rather than floating away.
    await expect
      .poll(async () => (await heroTransform(gameInspector, eid)).posY, {
        timeout: 4000,
        intervals: [200],
      })
      .toBeLessThan(groundY + 0.2);
  });

  test('A/D changes the hero heading', async ({
    vibegamePage,
    gameInspector,
  }) => {
    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);

    const before = await heroTransform(gameInspector, eid);
    await pressKey(vibegamePage, 'KeyD', 700);
    await vibegamePage.waitForTimeout(150);
    const after = await heroTransform(gameInspector, eid);

    const turned = quatAngle(before, after);
    expect(
      turned,
      `hero heading should change when turning (turned ${turned.toFixed(2)} rad)`
    ).toBeGreaterThan(0.1);
  });
});
