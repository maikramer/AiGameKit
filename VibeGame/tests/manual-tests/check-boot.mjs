// Boots the game and asserts: canvas present, hero entity has a physics body,
// no real console/page errors (WebGPU->WebGL2 fallback warning is benign).
import { withGame, heroDebug, errorsFrom, report } from './lib.mjs';

await withGame(async (page, { logs }) => {
  const canvas = await page.$('#game-canvas');
  const h = await heroDebug(page);

  let ok = true;
  ok &= report('canvas present', !!canvas);
  ok &= report('__heroDebug available', !!h, h ? '' : 'example bridge missing');
  ok &= report('hero has rapier body', h && h.bodyType >= 0, `bodyType=${h?.bodyType}`);

  const real = errorsFrom(logs).filter(
    (l) => !/WebGPU|adapters|deprecated|rapier/i.test(l.text)
  );
  ok &= report('no real console errors', real.length === 0, JSON.stringify(real));

  process.exit(ok ? 0 : 1);
});
