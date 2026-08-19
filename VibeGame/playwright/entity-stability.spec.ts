import { test, expect } from './fixtures/vibegame-fixtures';
import { heroEid, pressKey, waitForHeroReady } from './helpers/interaction';

const BENIGN = [/deprecated/i, /REGL/i, /rapier/i, /chrome-extension/i];

/**
 * Guards the recycle/lifecycle fixes (particles via onDestroy, projectile
 * cleanup, melee sidecars): transient gameplay entities must return to a
 * bounded count instead of leaking. A monotonic climb here would catch a
 * spawner/cleanup regression.
 */
test.describe('lifecycle: transient entities do not leak', () => {
  test('repeated attacks settle back to a bounded entity count', async ({
    vibegamePage,
    gameInspector,
  }) => {
    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);

    const baseline = (await gameInspector.entities()).length;

    // Spam the attack/chop action; this can spawn particles, floating text,
    // and impact FX — all of which are meant to self-destruct.
    for (let i = 0; i < 15; i++) {
      await pressKey(vibegamePage, 'KeyJ', 120);
      await vibegamePage.waitForTimeout(120);
    }

    // Let one-shots, particle bursts and floating text expire.
    await vibegamePage.waitForTimeout(3000);

    const settled = (await gameInspector.entities()).length;
    expect(
      settled,
      `entity count should settle near baseline (was ${baseline}, now ${settled})`
    ).toBeLessThanOrEqual(baseline + 25);

    const consoleErrors = (await gameInspector.captureConsoleErrors()).filter(
      (e) => !BENIGN.some((p) => p.test(e))
    );
    expect(
      consoleErrors,
      `attacking should not log errors. Got: ${JSON.stringify(consoleErrors)}`
    ).toEqual([]);
  });

  test('idle frames keep the entity count stable', async ({
    gameInspector,
  }) => {
    const a = (await gameInspector.entities()).length;
    for (let i = 0; i < 30; i++) await gameInspector.step(1 / 60);
    const b = (await gameInspector.entities()).length;
    expect(
      Math.abs(b - a),
      `idle stepping should not churn entities (${a} -> ${b})`
    ).toBeLessThanOrEqual(5);
  });
});
