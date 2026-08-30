import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { ParticleEmitter } from '../../../src/plugins/particles/components';

const DEG = Math.PI / 180;

describe('particles ParticleEmitter table-driven', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  for (let i = 0; i < 100; i++) {
    it(`emitter field roundtrip entity slot ${i}`, () => {
      const eid = state.createEntity();
      state.addComponent(eid, ParticleEmitter);

      const rate = 10 + (i % 20);
      const lifeMin = 0.5 + (i % 5) * 0.1;
      const lifeMax = lifeMin + 0.5;
      const speedMin = 1 + (i % 7) * 0.2;
      const speedMax = speedMin + 2;
      const sizeMin = 0.1 + (i % 4) * 0.05;
      const sizeMax = sizeMin + 0.3;
      const r = (i % 256) / 255;
      const g = ((i * 3) % 256) / 255;
      const b = ((i * 7) % 256) / 255;

      ParticleEmitter.active[eid] = 1;
      ParticleEmitter.preset[eid] = i % 8;
      ParticleEmitter.emissionRate[eid] = rate;
      ParticleEmitter.duration[eid] = 3 + (i % 10);
      ParticleEmitter.startLifeMin[eid] = lifeMin;
      ParticleEmitter.startLifeMax[eid] = lifeMax;
      ParticleEmitter.startSpeedMin[eid] = speedMin;
      ParticleEmitter.startSpeedMax[eid] = speedMax;
      ParticleEmitter.startSizeMin[eid] = sizeMin;
      ParticleEmitter.startSizeMax[eid] = sizeMax;
      ParticleEmitter.startColorR[eid] = r;
      ParticleEmitter.startColorG[eid] = g;
      ParticleEmitter.startColorB[eid] = b;
      ParticleEmitter.startColorA[eid] = 0.9;
      ParticleEmitter.worldSpace[eid] = i % 2;
      ParticleEmitter.looping[eid] = (i + 1) % 2;
      ParticleEmitter.shapeRadius[eid] = 0.5 + (i % 6) * 0.1;
      ParticleEmitter.shapeAngle[eid] = (i % 90) * DEG;

      expect(ParticleEmitter.emissionRate[eid]).toBeCloseTo(rate, 5);
      expect(ParticleEmitter.startLifeMin[eid]).toBeCloseTo(lifeMin, 5);
      expect(ParticleEmitter.startLifeMax[eid]).toBeCloseTo(lifeMax, 5);
      expect(ParticleEmitter.startSpeedMin[eid]).toBeCloseTo(speedMin, 5);
      expect(ParticleEmitter.startSpeedMax[eid]).toBeCloseTo(speedMax, 5);
      expect(ParticleEmitter.startSizeMin[eid]).toBeCloseTo(sizeMin, 5);
      expect(ParticleEmitter.startColorR[eid]).toBeCloseTo(r, 5);
      expect(ParticleEmitter.active[eid]).toBe(1);
      expect(ParticleEmitter.looping[eid]).toBe((i + 1) % 2);
    });
  }
});
