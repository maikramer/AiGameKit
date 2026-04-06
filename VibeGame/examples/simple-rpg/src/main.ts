import {
  applyEquirectSkyEnvironment,
  configure,
  run,
} from 'vibegame';

async function bootstrap(): Promise<void> {
  configure({ canvas: '#game-canvas' });
  const runtime = await run();
  const state = runtime.getState();

  try {
    await applyEquirectSkyEnvironment(state, '/assets/sky/sky.png');
  } catch {
    console.warn('[simple-rpg] Sky env map not loaded (optional).');
  }
}

void bootstrap();
