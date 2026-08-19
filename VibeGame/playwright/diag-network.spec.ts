import { test } from '@playwright/test';

test('probe: rede + transcoder + GLB fetch', async ({ page }) => {
  const requests: string[] = [];
  page.on('request', (r) => {
    const u = r.url();
    if (u.includes('unpkg') || u.includes('basis') || u.includes('glb'))
      requests.push(`REQ ${u}`);
  });
  page.on('requestfailed', (r) => {
    requests.push(`FAIL ${r.url()} :: ${r.failure()?.errorText}`);
  });

  await page.goto('/');
  await page.waitForTimeout(5000);

  const probe = await page.evaluate(async () => {
    const out: Record<string, unknown> = {};
    out.online = navigator.onLine;
    out.hwConcurrency = navigator.hardwareConcurrency;

    async function timeFetch(url: string) {
      const t0 = performance.now();
      try {
        const r = await fetch(url, { cache: 'no-store' });
        const len = (await r.blob()).size;
        return `${r.status} ${len}B em ${(performance.now() - t0).toFixed(0)}ms`;
      } catch (e) {
        return `ERRO em ${(performance.now() - t0).toFixed(0)}ms: ${String(e)}`;
      }
    }

    out.glb_local = await timeFetch(
      '/assets/meshes/village/torch_post_lod0.glb'
    );
    out.glb_local_2 = await timeFetch(
      '/assets/meshes/infra/city_wall_seg_b_lod0.glb'
    );
    out.unpkg_js = await timeFetch(
      'https://unpkg.com/three@0.185.0/examples/jsm/libs/basis/basis_transcoder.js'
    );
    out.unpkg_wasm = await timeFetch(
      'https://unpkg.com/three@0.185.0/examples/jsm/libs/basis/basis_transcoder.wasm'
    );
    return out;
  });

  console.log('=== PROBE ===');
  console.log(JSON.stringify(probe, null, 2));
  console.log('=== REQUESTS (unpkg/basis/glb) ===');
  for (const r of requests.slice(0, 50)) console.log(r);

  await page.waitForTimeout(10000);
  const late = await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    const el = document.querySelector('#loading-screen, [class*=loading]');
    return { loadingText: el?.textContent?.slice(0, 200) ?? null };
  });
  console.log('=== LOADING APÓS 15s ===', JSON.stringify(late));
});
