// Verifies tank-style steering: A/D rotate the third-person camera, and W moves
// along the camera's current forward axis (not a fixed world direction). After
// turning, the same W key must produce a different world heading.
import { withGame, focusCanvas, holdKey, heroDebug, report } from './lib.mjs';

function headingOf(a, b) {
  return Math.atan2(b.x - a.x, b.z - a.z);
}

await withGame(async (page) => {
  await focusCanvas(page);

  // First W burst — record heading A.
  const a0 = await heroDebug(page);
  await holdKey(page, 'KeyW', 700);
  const a1 = await heroDebug(page);
  const headingA = headingOf(a0, a1);

  // Turn the camera with D, then W again — record heading B.
  await holdKey(page, 'KeyD', 700);
  const b0 = await heroDebug(page);
  await holdKey(page, 'KeyW', 700);
  const b1 = await heroDebug(page);
  const headingB = headingOf(b0, b1);

  let diff = Math.abs(headingB - headingA);
  if (diff > Math.PI) diff = 2 * Math.PI - diff;
  const movedA = Math.hypot(a1.x - a0.x, a1.z - a0.z);
  const movedB = Math.hypot(b1.x - b0.x, b1.z - b0.z);

  let ok = true;
  ok &= report('W moves before turning', movedA > 0.5, `${movedA.toFixed(2)}m`);
  ok &= report('W moves after turning', movedB > 0.5, `${movedB.toFixed(2)}m`);
  ok &= report(
    'W heading changed after steering with D',
    diff > 0.15,
    `Δheading ${(diff * 180 / Math.PI).toFixed(0)}°`
  );

  process.exit(ok ? 0 : 1);
});
