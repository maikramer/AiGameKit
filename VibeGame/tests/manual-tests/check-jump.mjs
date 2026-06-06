// Regression guard: pressing Space must launch the hero off the ground and land.
// Root cause once: the player's Rigidbody.gravityScale defaulted to 0 (Float32
// store) so jumpVelocity = sqrt(2·|g·0|·h) = 0 — jump silently did nothing.
import { withGame, focusCanvas, report } from './lib.mjs';

await withGame(async (page) => {
  await focusCanvas(page);
  const y = () => page.evaluate(() => window.__heroDebug().y);
  const grounded = () => page.evaluate(() => window.__heroDebug().grounded);

  const startY = await y();
  await page.keyboard.down('Space');
  await page.waitForTimeout(80);
  await page.keyboard.up('Space');

  let peak = startY;
  let leftGround = false;
  for (let i = 0; i < 12; i++) {
    peak = Math.max(peak, await y());
    if ((await grounded()) === 0) leftGround = true;
    await page.waitForTimeout(60);
  }
  // settle back down
  await page.waitForTimeout(400);
  const endGrounded = (await grounded()) === 1;
  const endY = await y();

  let ok = true;
  ok &= report(
    'hero left the ground',
    leftGround,
    `peak +${(peak - startY).toFixed(2)}m`
  );
  ok &= report(
    'jump cleared a meaningful height',
    peak - startY > 0.8,
    `+${(peak - startY).toFixed(2)}m`
  );
  ok &= report(
    'landed back on the ground',
    endGrounded && Math.abs(endY - startY) < 0.6,
    `Δ${(endY - startY).toFixed(2)}m`
  );
  process.exit(ok ? 0 : 1);
});
