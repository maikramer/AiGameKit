import { test } from '@playwright/test';
import { GameInspector } from './helpers/game-inspector';

test.setTimeout(240_000);

test('hero: fetch e scheduler', async ({ page }) => {
  const heroReqs: string[] = [];
  page.on('request', (r) => {
    if (/hero|character/i.test(r.url())) heroReqs.push(`REQ ${r.url()}`);
  });
  page.on('requestfailed', (r) => {
    if (/hero|character/i.test(r.url()))
      heroReqs.push(`FAIL ${r.url()} :: ${r.failure()?.errorText}`);
  });
  page.on('response', (r) => {
    if (/hero|character/i.test(r.url()))
      heroReqs.push(`RESP ${r.status()} ${r.url()}`);
  });

  await page.goto('/');
  const inspector = new GameInspector(page);
  await inspector.waitForBridge(60_000);
  await page.waitForTimeout(45_000);

  const info = await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    const b = w.__VIBEGAME__ as Record<string, unknown> | undefined;
    const resources = performance
      .getEntriesByType('resource')
      .filter((e) => /hero/i.test(e.name))
      .map((e) => `${e.name.split('/').pop()} ${e.duration.toFixed(0)}ms`);
    return {
      heroResources: resources,
      bridgeStateKeys: b?.state
        ? Object.keys(b.state as object).slice(0, 30)
        : null,
      bridgeDebugKeys: b?.debug
        ? Object.keys(b.debug as object).slice(0, 30)
        : null,
    };
  });
  console.log('=== HERO RESOURCE TIMING ===');
  console.log(JSON.stringify(info.heroResources, null, 2));
  console.log('=== HERO REQUESTS ===');
  for (const r of heroReqs) console.log(r);
  console.log('=== BRIDGE state/debug keys ===');
  console.log(
    JSON.stringify(
      { state: info.bridgeStateKeys, debug: info.bridgeDebugKeys },
      null,
      2
    )
  );

  const player = await inspector.entity('player');
  console.log(
    '=== player-gltf-config ===',
    JSON.stringify(player?.components?.['player-gltf-config'] ?? null)
  );
  console.log(
    '=== spawn-gate ===',
    JSON.stringify(player?.components?.['spawn-gate'] ?? null)
  );
});
