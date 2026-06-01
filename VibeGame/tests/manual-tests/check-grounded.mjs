// Regression guard for the "walking plays the jump/fall animation" bug.
// While holding W on flat terrain the kinematic controller must stay grounded;
// if computedGrounded() flickers to 0 the animation system flips to FALLING.
import { withGame, sampleWhileHolding, report } from './lib.mjs';

await withGame(async (page) => {
  const rows = await sampleWhileHolding(page, 'KeyW', {
    samples: 25,
    intervalMs: 80,
    project: () => {
      const h = window.__heroDebug();
      const s = window.__heroState;
      const hero = s.getEntityByName('hero');
      const CM = s.getComponent('character-movement');
      const PC = s.getComponent('player-controller');
      return {
        y: +h.y.toFixed(3),
        gap: +h.groundGap.toFixed(3),
        grounded: h.grounded,
        vy: CM ? +CM.velocityY[hero].toFixed(2) : null,
        jumping: PC ? PC.isJumping[hero] : null,
      };
    },
  });

  console.log(' t | y       gap    grnd vy     jump');
  rows.forEach((r, i) =>
    console.log(
      `${String(i).padStart(2)} | ${String(r.y).padStart(7)} ${String(r.gap).padStart(6)}  ${r.grounded}   ${String(r.vy).padStart(6)}  ${r.jumping}`
    )
  );

  // Ignore the first couple of samples (settle), require grounded afterwards.
  const steady = rows.slice(3);
  const ungrounded = steady.filter((r) => r.grounded !== 1).length;
  const ok = report(
    'grounded stays 1 while walking',
    ungrounded === 0,
    `${ungrounded}/${steady.length} samples ungrounded`
  );
  process.exit(ok ? 0 : 1);
});
