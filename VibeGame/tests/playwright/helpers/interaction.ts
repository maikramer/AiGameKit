import type { Page } from '@playwright/test';

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
