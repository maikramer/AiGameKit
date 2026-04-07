import { test, expect } from './fixtures/vibegame-fixtures';

test.describe('visual-regression: simple-rpg', () => {
  test('game bridge is populated', async ({ gameInspector }) => {
    const componentNames = await gameInspector.componentNames();
    expect(componentNames.length).toBeGreaterThan(0);

    const entities = await gameInspector.entities();
    expect(entities.length).toBeGreaterThan(0);
  });

  test('state snapshot contains water data', async ({ gameInspector }) => {
    const snapshot = await gameInspector.snapshot();
    expect(snapshot).toContain('water');
    expect(snapshot).toContain('terrain');
  });

  test('water entity has correct components', async ({ gameInspector }) => {
    const waterEntities = await gameInspector.query('water');
    expect(waterEntities.length).toBeGreaterThan(0);

    for (const eid of waterEntities) {
      const data = await gameInspector.component(eid, 'water');
      expect(
        data,
        'Water entity ' + eid + ' should have water component'
      ).not.toBeNull();
      expect(data!.size).toBeGreaterThan(0);
      expect(data!.waterLevel).toBeDefined();
    }
  });

  test('page screenshot is captured', async ({ vibegamePage }) => {
    await vibegamePage.waitForTimeout(3000);
    const screenshot = await vibegamePage.screenshot({ fullPage: true });
    expect(screenshot).toBeTruthy();
    expect(screenshot.length).toBeGreaterThan(1000);
    test.info().attach('simple-rpg-water.png', {
      body: screenshot,
      contentType: 'image/png',
    });
  });
});
