import { expect, test, type Page } from '@playwright/test';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HUD_HARNESS_ORIGIN = 'http://127.0.0.1:30990';
const EVIDENCE_PATH = resolve(
  dirname(fileURLToPath(import.meta.url)),
  '../../../.sisyphus/evidence/task-23-compass-rotate.png'
);

test.use({ baseURL: HUD_HARNESS_ORIGIN });

interface MarkSnapshot {
  label: string;
  opacity: number;
  translateX: number;
}

async function readCompassMarks(page: Page): Promise<MarkSnapshot[]> {
  return page.locator('.vibe-compass-mark').evaluateAll((marks) =>
    marks.map((el) => {
      const label = el.getAttribute('data-cardinal') ?? '';
      const cs = window.getComputedStyle(el);
      const opacity = Number(cs.opacity);
      const transform = cs.transform;
      let translateX = Number.NaN;
      const match = /matrix\(([^)]+)\)/.exec(transform);
      if (match) {
        translateX = Number(match[1].split(',')[4]);
      }
      return { label, opacity, translateX };
    })
  );
}

async function setCameraDir(page: Page, x: number, z: number): Promise<void> {
  await page.evaluate(
    ([dx, dz]) => {
      const w = window as unknown as {
        __compassSetDir?: (x: number, z: number) => void;
      };
      w.__compassSetDir?.(dx, dz);
    },
    [x, z]
  );
}

function mostCentralMark(marks: MarkSnapshot[]): MarkSnapshot {
  let best = marks[0];
  let bestScore = Number.POSITIVE_INFINITY;
  for (const m of marks) {
    if (!Number.isFinite(m.translateX)) continue;
    const score = Math.abs(m.translateX) + (1 - m.opacity) * 1000;
    if (score < bestScore) {
      bestScore = score;
      best = m;
    }
  }
  return best;
}

test.describe('Compass widget — cardinal scrolling with camera yaw', () => {
  test('N is centred when the camera faces +Z (north), E when it faces +X', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(page.locator('.vibe-compass')).toBeAttached({
      timeout: 10000,
    });
    await expect(page.locator('.vibe-compass-mark')).toHaveCount(8, {
      timeout: 10000,
    });

    await setCameraDir(page, 0, 1);
    await page.waitForTimeout(150);

    let marks = await readCompassMarks(page);
    let centred = mostCentralMark(marks);
    expect(centred.label).toBe('N');
    expect(centred.opacity).toBeGreaterThan(0.98);

    await setCameraDir(page, 1, 0);
    await page.waitForTimeout(150);

    marks = await readCompassMarks(page);
    centred = mostCentralMark(marks);
    expect(centred.label).toBe('E');
    expect(centred.opacity).toBeGreaterThan(0.98);

    await page.screenshot({
      path: EVIDENCE_PATH,
    });
  });

  test('the N mark uses the configured mark-color-north highlight', async ({
    page,
  }) => {
    await page.goto('/');
    await expect(
      page.locator('.vibe-compass-mark[data-cardinal="N"]')
    ).toBeAttached({ timeout: 10000 });

    const color = await page
      .locator('.vibe-compass-mark[data-cardinal="N"]')
      .evaluate((el) => window.getComputedStyle(el).color);

    expect(color.toLowerCase()).toMatch(/rgb\(/);
    expect(color).toContain('255');
  });
});
