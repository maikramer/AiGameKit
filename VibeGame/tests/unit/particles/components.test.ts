import { describe, expect, it } from 'bun:test';
import { ParticleEmitter } from 'vibegame';

describe('ParticleEmitter component', () => {
  it('expõe todos os campos esperados', () => {
    const keys = Object.keys(ParticleEmitter);
    expect(keys).toContain('active');
    expect(keys).toContain('preset');
    expect(keys).toContain('emissionRate');
    expect(keys).toContain('duration');
    expect(keys).toContain('startLifeMin');
    expect(keys).toContain('startLifeMax');
    expect(keys).toContain('startSpeedMin');
    expect(keys).toContain('startSpeedMax');
    expect(keys).toContain('startSizeMin');
    expect(keys).toContain('startSizeMax');
    expect(keys).toContain('startColorR');
    expect(keys).toContain('startColorG');
    expect(keys).toContain('startColorB');
    expect(keys).toContain('startColorA');
    expect(keys).toContain('worldSpace');
    expect(keys).toContain('renderMode');
    expect(keys).toContain('looping');
    expect(keys).toContain('burst');
    expect(keys).toContain('burstCount');
    expect(keys).toContain('shapeRadius');
    expect(keys).toContain('shapeAngle');
    expect(keys).toHaveLength(21);
  });

  it('campos flag são Uint8Array', () => {
    expect(ParticleEmitter.active).toBeInstanceOf(Uint8Array);
    expect(ParticleEmitter.preset).toBeInstanceOf(Uint8Array);
    expect(ParticleEmitter.worldSpace).toBeInstanceOf(Uint8Array);
    expect(ParticleEmitter.renderMode).toBeInstanceOf(Uint8Array);
    expect(ParticleEmitter.looping).toBeInstanceOf(Uint8Array);
    expect(ParticleEmitter.burst).toBeInstanceOf(Uint8Array);
  });

  it('campos numéricos são Float32Array', () => {
    expect(ParticleEmitter.emissionRate).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.duration).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startLifeMin).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startLifeMax).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startSpeedMin).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startSpeedMax).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startSizeMin).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startSizeMax).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startColorR).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startColorG).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startColorB).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.startColorA).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.burstCount).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.shapeRadius).toBeInstanceOf(Float32Array);
    expect(ParticleEmitter.shapeAngle).toBeInstanceOf(Float32Array);
  });

  it('todos os arrays têm o mesmo comprimento (MAX_ENTITIES)', () => {
    const len = ParticleEmitter.active.length;
    expect(len).toBeGreaterThan(0);
    for (const arr of Object.values(ParticleEmitter)) {
      expect((arr as { length: number }).length).toBe(len);
    }
  });

  it('inicializam a zero', () => {
    const eid = 9999;
    expect(ParticleEmitter.active[eid]).toBe(0);
    expect(ParticleEmitter.preset[eid]).toBe(0);
    expect(ParticleEmitter.emissionRate[eid]).toBe(0);
    expect(ParticleEmitter.startColorR[eid]).toBe(0);
    expect(ParticleEmitter.burst[eid]).toBe(0);
    expect(ParticleEmitter.looping[eid]).toBe(0);
  });
});
