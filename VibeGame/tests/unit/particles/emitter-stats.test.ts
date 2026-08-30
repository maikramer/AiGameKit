import { describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import {
  TransformsPlugin,
  WorldTransform,
} from '../../../src/plugins/transforms';
import { ParticleEmitter } from '../../../src/plugins/particles/components';
import { describeParticleEmitters } from '../../../src/plugins/particles/systems';

/** The particle census backs the bridge's `particles()` — cap starvation and
 *  cull state must be observable per emitter. */
describe('describeParticleEmitters', () => {
  it('reports cap, active flags and world positions per emitter', () => {
    const state = new State();
    state.registerPlugin(TransformsPlugin);

    const a = state.createEntity();
    const b = state.createEntity();
    state.addComponent(a, ParticleEmitter, { active: 1 });
    state.addComponent(b, ParticleEmitter, { active: 0 });
    state.addComponent(b, WorldTransform);
    WorldTransform.posX[b] = 12;
    WorldTransform.posY[b] = 134;
    WorldTransform.posZ[b] = -8;

    const stats = describeParticleEmitters(state);

    expect(stats.cap).toBeGreaterThan(0);
    expect(stats.total).toBe(2);
    expect(stats.attached + stats.detached).toBe(stats.total);

    const rowA = stats.emitters.find((r) => r.eid === a)!;
    const rowB = stats.emitters.find((r) => r.eid === b)!;
    expect(rowA.active).toBe(true);
    expect(rowA.attached).toBe(true);
    expect(rowB.active).toBe(false);
    expect(rowB.pos).toEqual({ x: 12, y: 134, z: -8 });
  });

  it('reports an empty census with the cap on a fresh state', () => {
    const stats = describeParticleEmitters(new State());
    expect(stats).toEqual({
      cap: expect.any(Number),
      total: 0,
      attached: 0,
      detached: 0,
      emitters: [],
    });
  });
});
