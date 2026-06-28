import { expect, test } from '@playwright/test';

interface HeroState {
  y: number;
  vy: number;
}

test.describe('hero fall repro', () => {
  test('herói não deve cair abaixo do terreno na inicialização', async ({
    page,
  }) => {
    const errors: string[] = [];
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push(msg.text());
    });

    await page.goto('/');
    await expect(page.locator('#game-canvas')).toBeVisible({ timeout: 30000 });
    await page.waitForFunction(
      () => !!(window as unknown as Record<string, unknown>).__VIBEGAME__,
      undefined,
      { timeout: 45000 }
    );

    const getHeroY = async (): Promise<HeroState> => {
      return page.evaluate(() => {
        const bridge = (window as unknown as Record<string, unknown>)
          .__VIBEGAME__ as {
          entity: (name: string) => {
            eid: number;
            components: Record<string, Record<string, number>>;
          } | null;
          component: (
            eid: number,
            name: string
          ) => Record<string, number> | null;
        };
        const hero = bridge.entity('hero');
        if (!hero) return { y: NaN, vy: NaN };
        const transform = bridge.component(hero.eid, 'Transform');
        const body = bridge.component(hero.eid, 'Body');
        return {
          y: transform?.y ?? NaN,
          vy: body?.linVelY ?? NaN,
        };
      });
    };

    const samples: HeroState[] = [];
    const startTime = Date.now();
    while (Date.now() - startTime < 5000) {
      samples.push(await getHeroY());
      await page.waitForTimeout(250);
    }

    const ys = samples.map((s) => s.y).filter((y) => Number.isFinite(y));
    const minY = Math.min(...ys);
    const finalY = ys[ys.length - 1];
    const lastThird = ys.slice(Math.floor(ys.length * 0.66));
    const rangeLastThird = Math.max(...lastThird) - Math.min(...lastThird);

    console.log('Hero Y samples:', ys);
    console.log('Console errors:', errors);

    expect(minY, 'herói não deve cair abaixo de y=0').toBeGreaterThan(0);
    expect(
      finalY,
      'herói deve permanecer acima do terreno ao final'
    ).toBeGreaterThan(0);
    expect(
      rangeLastThird,
      'posição do herói deve estabilizar após spawn'
    ).toBeLessThan(5);

    const relevantErrors = errors.filter(
      (e) =>
        e.includes('terrain') ||
        e.includes('heightfield') ||
        e.includes('collider') ||
        e.includes('rapier') ||
        e.includes('body')
    );
    expect(relevantErrors, 'sem erros de terreno/física').toEqual([]);
  });
});
