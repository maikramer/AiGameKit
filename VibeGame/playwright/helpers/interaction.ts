import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { GameInspector } from './game-inspector';

/** Resolve the hero entity id, asserting it exists. */
export async function heroEid(inspector: GameInspector): Promise<number> {
  const hero = await inspector.entity('hero');
  expect(hero, 'hero entity should exist in the scene').not.toBeNull();
  return hero!.eid;
}

/**
 * Wait until the hero has finished the terrain-load settle: the example freezes
 * the hero at its spawn Y until the heightmap loads, then snaps it down. Movement
 * only integrates after that, so gameplay assertions must wait for it. Ready when
 * the CCT reports grounded, or its Y has stopped changing between samples.
 */
export async function waitForHeroReady(
  inspector: GameInspector,
  eid: number,
  timeout = 20000
): Promise<void> {
  let lastY = Number.NaN;
  await expect
    .poll(
      async () => {
        const cc = await inspector.component(eid, 'character-controller');
        const t = await inspector.component(eid, 'transform');
        const grounded = cc?.grounded === 1;
        const y = t?.posY ?? Number.NaN;
        const settled = Number.isFinite(lastY) && Math.abs(y - lastY) < 0.03;
        lastY = y;
        return grounded || settled;
      },
      { timeout, intervals: [300] }
    )
    .toBe(true);
}

export async function pressKey(
  page: Page,
  key: string,
  durationMs = 100
): Promise<void> {
  await page.keyboard.down(key);
  await page.waitForTimeout(durationMs);
  await page.keyboard.up(key);
}

export async function clickAt(page: Page, x: number, y: number): Promise<void> {
  await page.mouse.click(x, y);
}

export async function moveMouse(
  page: Page,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  steps = 10
): Promise<void> {
  await page.mouse.move(fromX, fromY);
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const x = Math.round(fromX + (toX - fromX) * t);
    const y = Math.round(fromY + (toY - fromY) * t);
    await page.mouse.move(x, y);
    await page.waitForTimeout(16);
  }
}

export async function dragCanvas(
  page: Page,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  steps = 10
): Promise<void> {
  await page.mouse.move(fromX, fromY);
  await page.mouse.down();
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const x = Math.round(fromX + (toX - fromX) * t);
    const y = Math.round(fromY + (toY - fromY) * t);
    await page.mouse.move(x, y);
    await page.waitForTimeout(16);
  }
  await page.mouse.up();
}

export async function typeText(page: Page, text: string): Promise<void> {
  await page.keyboard.type(text);
}
