// Mede clearance estrada−terreno por raycast denso ao longo dos ribbons.
import { chromium } from 'playwright-core';

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
await page.waitForTimeout(9000);
const out = await page.evaluate(async () => {
  const vg = window.__VIBEGAME__;
  const scene = vg.scene?.() ?? vg.rendering?.()?.scene;
  const THREE = vg.rendering?.()?.THREE;
  // Sem THREE exposto: usar construtores dos objetos da cena.
  const roads = [];
  const terrains = [];
  scene.traverse((o) => {
    if (!o.isMesh) return;
    if (o.geometry?.getAttribute?.('color')?.itemSize === 4) roads.push(o);
    // Terreno = CustomShaderMaterial opaco ('__csm' no material) — os chunks
    // do TerrainLOD; a água também é CSM mas é transparente.
    else if (o.material && '__csm' in o.material && !o.material.transparent)
      terrains.push(o);
  });
  // Altura do terreno em (x,z): baricêntrica em XZ sobre os triângulos do
  // chunk cujo bbox mundial contém o ponto (interp = superfície renderizada).
  const V = scene.position.constructor; // THREE.Vector3 via instância
  const tmp = new V();
  for (const t of terrains) {
    t.updateWorldMatrix(true, false);
    t.geometry.computeBoundingBox();
  }
  function terrainYAt(x, z) {
    let best = null;
    for (const t of terrains) {
      const bb = t.geometry.boundingBox;
      // bbox em mundo (assumindo sem rotação nos chunks de terreno)
      const min = tmp.copy(bb.min).applyMatrix4(t.matrixWorld).clone();
      const max = tmp.copy(bb.max).applyMatrix4(t.matrixWorld).clone();
      const x0 = Math.min(min.x, max.x), x1 = Math.max(min.x, max.x);
      const z0 = Math.min(min.z, max.z), z1 = Math.max(min.z, max.z);
      if (x < x0 || x > x1 || z < z0 || z > z1) continue;
      const pos = t.geometry.getAttribute('position');
      const idx = t.geometry.getIndex();
      const n = idx ? idx.count : pos.count;
      const a = new V(), b = new V(), c = new V();
      for (let i = 0; i + 2 < n; i += 3) {
        const ia = idx ? idx.getX(i) : i;
        const ib = idx ? idx.getX(i + 1) : i + 1;
        const ic = idx ? idx.getX(i + 2) : i + 2;
        a.fromBufferAttribute(pos, ia).applyMatrix4(t.matrixWorld);
        b.fromBufferAttribute(pos, ib).applyMatrix4(t.matrixWorld);
        c.fromBufferAttribute(pos, ic).applyMatrix4(t.matrixWorld);
        const d = (b.z - c.z) * (a.x - c.x) + (c.x - b.x) * (a.z - c.z);
        if (Math.abs(d) < 1e-9) continue;
        const w1 = ((b.z - c.z) * (x - c.x) + (c.x - b.x) * (z - c.z)) / d;
        const w2 = ((c.z - a.z) * (x - c.x) + (a.x - c.x) * (z - c.z)) / d;
        const w3 = 1 - w1 - w2;
        if (w1 < -1e-4 || w2 < -1e-4 || w3 < -1e-4) continue;
        const y = w1 * a.y + w2 * b.y + w3 * c.y;
        if (best === null || y > best) best = y;
      }
    }
    return best;
  }
  const results = [];
  for (const road of roads) {
    const pos = road.geometry.getAttribute('position');
    let minClear = Infinity;
    let buried = 0;
    let checked = 0;
    for (let i = 0; i + 4 < pos.count; i += 4) {
      for (const lane of [1, 2]) {
        for (const f of [0, 0.5]) {
          const x = pos.getX(i + lane) * (1 - f) + pos.getX(i + 4 + lane) * f;
          const y = pos.getY(i + lane) * (1 - f) + pos.getY(i + 4 + lane) * f;
          const z = pos.getZ(i + lane) * (1 - f) + pos.getZ(i + 4 + lane) * f;
          const ty = terrainYAt(x, z);
          if (ty === null) continue;
          const clear = y - ty;
          checked++;
          if (clear < minClear) minClear = clear;
          if (clear < -0.01) buried++;
        }
      }
    }
    results.push({ checked, minClear: +minClear.toFixed(3), buried });
  }
  return JSON.stringify({ roads: roads.length, terrains: terrains.length, results });
});
console.log(out);
await browser.close();
