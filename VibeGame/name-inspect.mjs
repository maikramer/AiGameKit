import { chromium } from 'playwright-core';
const browser = await chromium.launch({ headless: true, executablePath: '/usr/bin/google-chrome' });
const page = await browser.newPage();
await page.goto('http://localhost:3011/', { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForFunction(() => {
  const o = Array.from(document.querySelectorAll('div')).find((e) => e.textContent?.includes('A preparar o mundo'));
  if (!o) return true;
  const st = getComputedStyle(o);
  return st.display === 'none' || parseFloat(st.opacity) < 0.05;
}, undefined, { timeout: 150000, polling: 500 });
await page.waitForTimeout(3000);
const out = await page.evaluate(() => {
  const vg = window.__VIBEGAME__;
  const scene = vg.scene?.() ?? vg.rendering?.()?.scene;
  const counts = {};
  scene.traverse((o) => {
    if (!o.isMesh) return;
    const key = (o.name || o.parent?.name || o.type) + '|' + (o.geometry?.getAttribute('position')?.count ?? 0);
    counts[key] = (counts[key] ?? 0) + 1;
  });
  return JSON.stringify(Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0, 25));
});
console.log(out);
await browser.close();
