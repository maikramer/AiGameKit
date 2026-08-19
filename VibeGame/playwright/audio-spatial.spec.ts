import { test, expect } from './fixtures/vibegame-fixtures';
import { heroEid, waitForHeroReady } from './helpers/interaction';

type AudioBridge = {
  playSoundAt: (key: string, x: number, y: number, z: number) => { id: number };
  clearLog: () => void;
  snapshot: () => {
    events: Array<{ kind: string; key: string; detail?: string }>;
    active: Array<{ key: string }>;
  };
  setListenerPos: (x: number, y: number, z: number) => void;
  getListenerPos: () => { x: number; y: number; z: number } | null;
};

async function waitForAudioBridge(
  page: {
    waitForFunction: (
      fn: () => boolean,
      arg?: undefined,
      opts?: { timeout?: number }
    ) => Promise<unknown>;
  },
  timeout = 15000
): Promise<void> {
  await page.waitForFunction(
    () => {
      const w = window as unknown as {
        __VIBEGAME__?: { audio?: { playSoundAt?: unknown } };
      };
      return typeof w.__VIBEGAME__?.audio?.playSoundAt === 'function';
    },
    undefined,
    { timeout }
  );
}

test.describe('audio spatial cull (simple-rpg)', () => {
  test('far world SFX are skipped; near ones play', async ({
    vibegamePage,
    gameInspector,
  }) => {
    // Fixture already loaded `/` and waited for ECS bridge. Arm audio tab
    // without a full remount (avoids double load + race with loading gate).
    await vibegamePage.evaluate(() => {
      const w = window as unknown as {
        __VIBEGAME__?: { profiler?: { setTab?: (t: string) => void } };
      };
      w.__VIBEGAME__?.profiler?.setTab?.('audio');
    });
    await waitForAudioBridge(vibegamePage);

    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);
    const heroT = await gameInspector.component(eid, 'transform');
    expect(heroT).not.toBeNull();

    const result = await vibegamePage.evaluate(
      ([hx, hy, hz]) => {
        const audio = (
          window as unknown as { __VIBEGAME__: { audio: AudioBridge } }
        ).__VIBEGAME__.audio;
        // Pin listener at hero so cull math is deterministic even if the
        // camera system has not written setAudioListenerWorldPos yet.
        audio.setListenerPos(hx, hy, hz);
        audio.clearLog();

        const near = audio.playSoundAt('enemy-hurt', hx + 4, hy, hz + 2);
        const far = audio.playSoundAt('enemy-hurt', hx + 120, hy, hz);
        const snap = audio.snapshot();
        const plays = snap.events.filter(
          (e) => e.kind === 'play' && e.key === 'enemy-hurt'
        );
        const skips = snap.events.filter(
          (e) => e.kind === 'skip' && e.key === 'enemy-hurt'
        );
        return {
          nearId: near.id,
          farId: far.id,
          playCount: plays.length,
          skipCount: skips.length,
          skipDetail: skips[0]?.detail ?? '',
          listener: audio.getListenerPos(),
        };
      },
      [heroT!.posX, heroT!.posY, heroT!.posZ] as [number, number, number]
    );

    expect(
      result.nearId,
      'near hurt should allocate a howl id'
    ).toBeGreaterThan(0);
    expect(result.farId, 'far hurt must be culled (null handle)').toBe(-1);
    expect(result.playCount, 'exactly one play event').toBe(1);
    expect(result.skipCount, 'exactly one skip event').toBe(1);
    expect(result.skipDetail).toMatch(/cull/i);
  });

  test('after load, no rogue world SFX while idle in town', async ({
    vibegamePage,
    gameInspector,
  }) => {
    await waitForAudioBridge(vibegamePage);

    const eid = await heroEid(gameInspector);
    await waitForHeroReady(gameInspector, eid);

    // Clear preload/BGM noise, then watch a quiet window.
    await vibegamePage.evaluate(() => {
      const audio = (
        window as unknown as { __VIBEGAME__: { audio: AudioBridge } }
      ).__VIBEGAME__.audio;
      audio.clearLog();
    });
    await vibegamePage.waitForTimeout(2500);

    const rogue = await vibegamePage.evaluate(() => {
      const audio = (
        window as unknown as { __VIBEGAME__: { audio: AudioBridge } }
      ).__VIBEGAME__.audio;
      const snap = audio.snapshot();
      const worldKeys = new Set([
        'enemy-hurt',
        'enemy-death',
        'boss-roar',
        'item-drop',
        'mine-hit',
        'chop-hit',
        'mine-break',
        'chop-break',
        'bomb-drop',
      ]);
      const plays = snap.events.filter(
        (e) => e.kind === 'play' && worldKeys.has(e.key)
      );
      return {
        plays: plays.map((p) => p.key),
        activeWorld: snap.active
          .filter((a) => worldKeys.has(a.key))
          .map((a) => a.key),
      };
    });

    expect(
      rogue.plays,
      `idle town must not fire world SFX; got ${rogue.plays.join(',')}`
    ).toEqual([]);
    expect(rogue.activeWorld).toEqual([]);
  });
});
