import { describe, expect, it } from 'bun:test';
import { ParticleEmitter } from 'aigamekit-vibegame';
import { applyParticleDistanceCull } from '../../../src/plugins/particles/systems';
import type { ParticleSystem } from 'three.quarks';
import type { BatchedRenderer } from 'three.quarks';

/** `BatchedRenderer.update` steps every registered system, so what we assert
 *  is membership of the batch, not a visibility flag. */
function fakeRenderer() {
  const added: unknown[] = [];
  const deleted: unknown[] = [];
  return {
    added,
    deleted,
    addSystem: (ps: unknown) => added.push(ps),
    deleteSystem: (ps: unknown) => deleted.push(ps),
  };
}

function fakeSystem(x: number, y: number, z: number): ParticleSystem {
  return {
    emitter: { position: { x, y, z }, visible: true },
  } as unknown as ParticleSystem;
}

type Renderer = Pick<BatchedRenderer, 'addSystem' | 'deleteSystem'>;

describe('particle distance cull', () => {
  it('detaches an emitter past the cull radius', () => {
    const r = fakeRenderer();
    const ps = fakeSystem(0, 0, 400);
    const systems = new Map([[1, ps]]);
    const detached = new Set<number>();
    ParticleEmitter.burst[1] = 0;

    applyParticleDistanceCull(r as Renderer, systems, detached, 0, 0, 0);

    expect(r.deleted).toEqual([ps]);
    expect(detached.has(1)).toBe(true);
    expect(ps.emitter.visible).toBe(false);
  });

  it('leaves a near emitter in the batch', () => {
    const r = fakeRenderer();
    const ps = fakeSystem(0, 0, 20);
    const systems = new Map([[2, ps]]);
    const detached = new Set<number>();
    ParticleEmitter.burst[2] = 0;

    applyParticleDistanceCull(r as Renderer, systems, detached, 0, 0, 0);

    expect(r.deleted).toEqual([]);
    expect(r.added).toEqual([]);
    expect(detached.has(2)).toBe(false);
  });

  it('re-attaches once the camera comes back', () => {
    const r = fakeRenderer();
    const ps = fakeSystem(0, 0, 20);
    const systems = new Map([[3, ps]]);
    const detached = new Set<number>([3]);
    ParticleEmitter.burst[3] = 0;
    ps.emitter.visible = false;

    applyParticleDistanceCull(r as Renderer, systems, detached, 0, 0, 0);

    expect(r.added).toEqual([ps]);
    expect(detached.has(3)).toBe(false);
    expect(ps.emitter.visible).toBe(true);
  });

  it('does not flap in the hysteresis band', () => {
    const r = fakeRenderer();
    // Just inside the cull radius: a detached emitter must stay detached.
    const ps = fakeSystem(0, 0, 105);
    const systems = new Map([[4, ps]]);
    const detached = new Set<number>([4]);
    ParticleEmitter.burst[4] = 0;

    applyParticleDistanceCull(r as Renderer, systems, detached, 0, 0, 0);

    expect(r.added).toEqual([]);
    expect(detached.has(4)).toBe(true);
  });

  it('never culls a burst emitter (its entity is freed off ps.time)', () => {
    const r = fakeRenderer();
    const ps = fakeSystem(0, 0, 900);
    const systems = new Map([[5, ps]]);
    const detached = new Set<number>();
    ParticleEmitter.burst[5] = 1;

    applyParticleDistanceCull(r as Renderer, systems, detached, 0, 0, 0);

    expect(r.deleted).toEqual([]);
    expect(detached.has(5)).toBe(false);
  });
});
