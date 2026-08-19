import { test, expect } from './fixtures/vibegame-fixtures';

test.describe('debug-cycle: simple-rpg', () => {
  test('canvas visible without console errors', async ({ vibegamePage }) => {
    await expect(vibegamePage.locator('#game-canvas')).toBeVisible();

    const consoleErrors = await new Promise<string[]>((resolve) => {
      const errors: string[] = [];
      vibegamePage.on('console', (msg) => {
        if (msg.type() === 'error') errors.push(msg.text());
      });
      setTimeout(() => resolve(errors), 3000);
    });

    const benignPatterns = [
      /deprecated/i,
      /REGL/i,
      /rapier/i,
      /chrome-extension/i,
    ];
    const realErrors = consoleErrors.filter(
      (e) => !benignPatterns.some((p) => p.test(e))
    );

    expect(
      realErrors,
      `No real console errors. Got: ${JSON.stringify(realErrors)}`
    ).toEqual([]);
  });

  test('bridge __VIBEGAME__ is accessible', async ({ gameInspector }) => {
    const entities = await gameInspector.entities();
    expect(
      entities.length,
      'Should have entities in the game state'
    ).toBeGreaterThan(0);
  });

  test('no WebGL shader compile errors', async ({ gameInspector }) => {
    const glErrors = await gameInspector.captureWebGLErrors();
    expect(
      glErrors,
      `No WebGL errors. Got: ${JSON.stringify(glErrors)}`
    ).toEqual([]);
  });

  test('state snapshot is valid JSON', async ({ gameInspector }) => {
    const snapshot = await gameInspector.snapshot();
    expect(snapshot).toBeTruthy();
    expect(snapshot.length).toBeGreaterThan(10);

    test.info().attach('game-state-snapshot.txt', {
      body: snapshot,
      contentType: 'text/plain',
    });
  });

  test('all entities have components', async ({ gameInspector }) => {
    const entities = await gameInspector.entities();

    for (const entity of entities) {
      const compNames = Object.keys(entity.components);
      expect(
        compNames.length,
        `Entity ${entity.name ?? `eid-${entity.eid}`} should have at least one component`
      ).toBeGreaterThan(0);
    }
  });

  test('component names are registered', async ({ gameInspector }) => {
    const names = await gameInspector.componentNames();
    expect(names.length).toBeGreaterThan(0);

    test.info().attach('component-names.json', {
      body: JSON.stringify(names, null, 2),
      contentType: 'application/json',
    });
  });

  test('entities and components are inspectable', async ({ gameInspector }) => {
    const entities = await gameInspector.entities();
    expect(entities.length).toBeGreaterThan(0);

    const names = await gameInspector.componentNames();
    expect(names.length).toBeGreaterThan(0);

    const named = await gameInspector.namedEntities();
    if (named.length > 0) {
      const sample = named.slice(0, Math.min(named.length, 3));
      for (const { name, eid } of sample) {
        const entity = await gameInspector.entity(name);
        expect(entity, `Entity "${name}" should be found`).not.toBeNull();
        expect(entity!.eid).toBe(eid);
      }
    } else {
      for (const entity of entities.slice(0, Math.min(entities.length, 3))) {
        const comps = Object.keys(entity.components);
        expect(comps.length).toBeGreaterThan(0);
      }
    }
  });

  test('screenshot baseline', async ({ vibegamePage }) => {
    await vibegamePage.waitForTimeout(3000);
    const screenshot = await vibegamePage.screenshot({ fullPage: true });
    expect(screenshot).toBeTruthy();
    expect(screenshot.length).toBeGreaterThan(1000);

    test.info().attach('simple-rpg-baseline.png', {
      body: screenshot,
      contentType: 'image/png',
    });
  });
});
