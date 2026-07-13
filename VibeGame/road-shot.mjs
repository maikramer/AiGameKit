// Screenshot após andar: bun road-shot.mjs <out> <tecla> <ms>
import { chromium } from 'playwright-core';

const OUT = process.argv[2] ?? '/tmp/road-shot.jpeg';
const KEY = process.argv[3] ?? 'w';
const WALK_MS = Number(process.argv[4] ?? 2000);

const browser = await chromium.launch({
  headless: true,
  executablePath: '/usr/bin/google-chrome',
  args: ['--use-gl=angle', '--enable-webgl', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto('http://localhost:3011/', {
  waitUntil: 'domcontentloaded',
  timeout: 60000,
});
await page.waitForFunction(
  () => {
    const els = Array.from(document.querySelectorAll('div'));
    const overlay = els.find((e) =>
      e.textContent?.includes('A preparar o mundo')
    );
    if (!overlay) return true;
    const st = getComputedStyle(overlay);
    return st.display === 'none' || parseFloat(st.opacity) < 0.05;
  },
  undefined,
  { timeout: 150000, polling: 500 }
);
await page.waitForTimeout(3000);
await page
  .locator('canvas')
  .first()
  .click({ position: { x: 640, y: 400 } })
  .catch(() => {});
for (const step of KEY.split('+')) {
  const [k, ms] = step.split(':');
  await page.keyboard.down(k);
  await page.waitForTimeout(Number(ms ?? WALK_MS));
  await page.keyboard.up(k);
  await page.waitForTimeout(300);
}
await page.waitForTimeout(1500);
await page.screenshot({ path: OUT, type: 'jpeg', quality: 85 });
console.log('saved', OUT);
await browser.close();
