// Holds W and asserts the hero actually translates on the ground.
import { withGame, heroDebug, focusCanvas, holdKey, report } from './lib.mjs';

await withGame(async (page) => {
  const before = await heroDebug(page);
  await focusCanvas(page);
  await holdKey(page, 'KeyW', 2000);
  const after = await heroDebug(page);

  const dz = Math.abs(after.z - before.z);
  const dx = Math.abs(after.x - before.x);
  const moved = Math.hypot(dx, dz);

  let ok = true;
  ok &= report(
    'hero translated holding W',
    moved > 1,
    `moved ${moved.toFixed(2)}m`
  );
  ok &= report(
    'hero stayed near ground',
    Math.abs(after.groundGap) < 1,
    `gap ${after.groundGap.toFixed(3)}`
  );
  console.log(
    '  before:',
    JSON.stringify({ x: before.x, y: before.y, z: before.z })
  );
  console.log(
    '  after :',
    JSON.stringify({ x: after.x, y: after.y, z: after.z })
  );

  process.exit(ok ? 0 : 1);
});
