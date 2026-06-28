import { describe, expect, it } from 'bun:test';
import {
  ParticleEmitter,
  Transform,
  WorldTransform,
  spawnParticleBurst,
} from 'vibegame';
import type { ParticleBurstOptions, State } from 'vibegame';

function makeMockState(): State {
  let nextEid = 0;
  return {
    createEntity: () => nextEid++,
    addComponent: () => {},
  } as unknown as State;
}

describe('spawnParticleBurst', () => {
  it('cria entidade e posiciona no mundo', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, { x: 1, y: 2, z: 3 });
    expect(eid).toBe(0);
    expect(Transform.posX[eid]).toBe(1);
    expect(Transform.posY[eid]).toBe(2);
    expect(Transform.posZ[eid]).toBe(3);
    expect(WorldTransform.posX[eid]).toBe(1);
    expect(WorldTransform.posY[eid]).toBe(2);
    expect(WorldTransform.posZ[eid]).toBe(3);
  });

  it('restaura escala identidade (1,1,1) e rotação W=1', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, { x: 0, y: 0, z: 0 });
    expect(Transform.scaleX[eid]).toBe(1);
    expect(Transform.scaleY[eid]).toBe(1);
    expect(Transform.scaleZ[eid]).toBe(1);
    expect(Transform.rotW[eid]).toBe(1);
    expect(WorldTransform.scaleX[eid]).toBe(1);
    expect(WorldTransform.scaleY[eid]).toBe(1);
    expect(WorldTransform.scaleZ[eid]).toBe(1);
    expect(WorldTransform.rotW[eid]).toBe(1);
  });

  it('marca dirty no Transform', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, { x: 0, y: 0, z: 0 });
    expect(Transform.dirty[eid]).toBe(1);
  });

  it('configura ParticleEmitter como burst ativo', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, { x: 0, y: 0, z: 0 });
    expect(ParticleEmitter.active[eid]).toBe(1);
    expect(ParticleEmitter.burst[eid]).toBe(1);
    expect(ParticleEmitter.looping[eid]).toBe(0);
    expect(ParticleEmitter.worldSpace[eid]).toBe(0);
  });

  it('usa preset explosion (índice 5) por defeito', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, { x: 0, y: 0, z: 0 });
    expect(ParticleEmitter.preset[eid]).toBe(5);
  });

  it('respeita preset explícito', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, {
      x: 0,
      y: 0,
      z: 0,
      preset: 'fire',
    });
    expect(ParticleEmitter.preset[eid]).toBe(0);
  });

  it('aplica defaults count=60 e duration=0.5', () => {
    const state = makeMockState();
    const eid = spawnParticleBurst(state, { x: 0, y: 0, z: 0 });
    expect(ParticleEmitter.burstCount[eid]).toBe(60);
    expect(ParticleEmitter.duration[eid]).toBe(0.5);
  });

  it('respeita count e duration explícitos', () => {
    const state = makeMockState();
    const opts: ParticleBurstOptions = {
      x: 10,
      y: 20,
      z: 30,
      count: 120,
      duration: 1.5,
    };
    const eid = spawnParticleBurst(state, opts);
    expect(ParticleEmitter.burstCount[eid]).toBe(120);
    expect(ParticleEmitter.duration[eid]).toBe(1.5);
  });

  it('cria entidades com ids incrementais', () => {
    const state = makeMockState();
    const e1 = spawnParticleBurst(state, { x: 0, y: 0, z: 0 });
    const e2 = spawnParticleBurst(state, { x: 1, y: 1, z: 1 });
    expect(e1).toBe(0);
    expect(e2).toBe(1);
    expect(Transform.posX[e2]).toBe(1);
  });
});
