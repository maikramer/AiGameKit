import { State } from '../../../src/core';
import {
  HudScreenUpdateSystem,
  createHudScreenLayer,
} from '../../../src/plugins/hud/screen-layer';
import { FloatingTextPlugin } from '../../../src/plugins/floating-text/plugin';
import {
  spawnFloatingText,
  spawnFloatingTextScreen,
} from '../../../src/plugins/floating-text/utils';

async function bootstrap(): Promise<void> {
  const state = new State();
  state.registerSystem(HudScreenUpdateSystem);
  state.registerPlugin(FloatingTextPlugin);
  await state.initializePlugins();
  createHudScreenLayer(state);
  console.log(
    '[float-harness] ready; layer=',
    !!document.querySelector('.vibe-hud-screen-layer')
  );

  let frame = 0;
  let last = performance.now();
  function loop(now: number): void {
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    state.step(dt);
    frame++;
    if (frame % 30 === 0) {
      spawnFloatingTextScreen(state, `+${frame}`, {
        x: 120 + (frame % 200),
        y: 120,
        color: '#7CFC00',
      });
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);

  document.getElementById('spawn-screen')?.addEventListener('click', () => {
    spawnFloatingTextScreen(state, '+25 HP', {
      x: window.innerWidth / 2,
      y: window.innerHeight / 2,
      color: '#7CFC00',
      fontSizePx: 22,
    });
  });

  document.getElementById('spawn-world')?.addEventListener('click', () => {
    spawnFloatingText(state, 'WORLD', {
      x: 0,
      y: 1.2,
      z: 0,
      color: 0xffffff,
      space: 'world',
    });
  });

  document.getElementById('spawn-crit')?.addEventListener('click', () => {
    spawnFloatingTextScreen(state, 'CRIT 42!', {
      x: window.innerWidth / 2,
      y: window.innerHeight / 2 + 40,
      crit: true,
    });
  });
}

void bootstrap().catch((err) =>
  console.error('[float-harness] bootstrap failed:', err)
);
