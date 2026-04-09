import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { createEntityFromRecipe } from '../../../src/core/recipes/parser';
import { getParticlesContext } from '../../../src/plugins/particles/context';
import {
  ParticleBootstrapSystem,
  ParticleEmitSystem,
  ParticleRenderSystem,
} from '../../../src/plugins/particles/systems';
import { ParticlesPlugin } from '../../../src/plugins/particles/plugin';
import { RenderingPlugin, getRenderingContext } from 'vibegame/rendering';
import { TransformsPlugin } from 'vibegame/transforms';

describe('Particles runtime integration', () => {
  it('keeps particle systems alive and emitting after render updates', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(RenderingPlugin);
    state.registerPlugin(ParticlesPlugin);
    getRenderingContext(state);

    const entity = createEntityFromRecipe(state, 'particle-emitter', {
      preset: 'sparks',
      rate: 60,
      lifetime: 2,
      size: 0.5,
      transform: 'pos: 0 1 0',
    });

    ParticleBootstrapSystem.update?.(state);
    ParticleEmitSystem.update?.(state);

    const ctx = getParticlesContext(state);
    expect(ctx.batch).not.toBeNull();
    expect(ctx.roots.has(entity)).toBe(true);
    expect(ctx.batch!.systemToBatchIndex.size).toBe(1);

    for (let i = 0; i < 3; i++) {
      state.time.deltaTime = 0.1;
      ParticleRenderSystem.update?.(state);
    }

    expect(ctx.batch!.systemToBatchIndex.size).toBe(1);
    const [system] = Array.from(ctx.batch!.systemToBatchIndex.keys()) as Array<{
      particleNum?: number;
    }>;
    expect(system?.particleNum ?? 0).toBeGreaterThan(0);
  });
});
