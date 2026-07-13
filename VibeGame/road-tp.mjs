// Teleporta o herói e tira screenshot: bun road-tp.mjs <out> <x> <z> [lookKey]
import { chromium } from 'playwright-core';

const OUT = process.argv[2] ?? '/tmp/road-tp.jpeg';
const TX = Number(process.argv[3] ?? 40);
const TZ = Number(process.argv[4] ?? 0);

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
const result = await page.evaluate(
  ([tx, tz]) => {
    const vg = window.__VIBEGAME__;
    const eid = vg.entity('hero').eid ?? 15;
    const ph = typeof vg.physics === 'function' ? vg.physics() : vg.physics;
    const body = ph.entityToRigidbody.get(eid);
    const y = 42;
    const tr = typeof vg.component === 'function' ? vg.component('transform') : null;
    if (tr) {
      tr.posX[eid] = tx;
      tr.posY[eid] = y;
      tr.posZ[eid] = tz;
      if (tr.dirty) tr.dirty[eid] = 1;
    }
    body.setTranslation({ x: tx, y, z: tz }, true);
    return JSON.stringify({ eid, hasTr: !!tr, moved: body.translation() });
  },
  [TX, TZ]
);
console.log('[tp]', result);
await page.waitForTimeout(2500);
await page.screenshot({ path: OUT, type: 'jpeg', quality: 85 });
console.log('saved', OUT);
await browser.close();
