// Regression guard for "after ~20s of walking the hero desyncs/sinks into the
// terrain". With per-chunk heightfield colliders the hero must stay grounded and
// glued to the surface for the whole traversal (no growing float, no sinking).
import { withGame, sampleWhileHolding, report } from './lib.mjs';

await withGame(async (page) => {
  const rows = await sampleWhileHolding(page, 'KeyW', {
    samples: 40,
    intervalMs: 400, // ~16s of continuous walking
    project: () => {
      const h = window.__heroDebug();
      return {
        z: +h.z.toFixed(1),
        gap: +h.groundGap.toFixed(3),
        grounded: h.grounded,
      };
    },
  });

  const settled = rows.slice(3);
  const travelled = Math.abs(rows.at(-1).z - rows[0].z);
  const ungrounded = settled.filter((r) => r.grounded !== 1).length;
  // gap is hero-feet minus the fine heightmap sample; the chunk mesh/collider is
  // a coarser linear surface (~cell-sized triangles), so a sub-cell band on both
  // sides is expected and harmless — the hero rests on the rendered mesh. The bug
  // this guards against is unbounded drift (the old single-heightfield put the
  // hero ~16 m off), so we only require the gap to stay within roughly one cell.
  const minGap = Math.min(...settled.map((r) => r.gap));
  const maxGap = Math.max(...settled.map((r) => r.gap));

  let ok = true;
  ok &= report(
    'walked a long distance',
    travelled > 80,
    `${travelled.toFixed(0)}m`
  );
  ok &= report(
    'stayed grounded throughout',
    ungrounded === 0,
    `${ungrounded}/${settled.length} ungrounded`
  );
  ok &= report(
    'stays glued to terrain (no drift)',
    minGap > -1.5 && maxGap < 1.5,
    `gap ∈ [${minGap.toFixed(2)}, ${maxGap.toFixed(2)}]`
  );

  process.exit(ok ? 0 : 1);
});
