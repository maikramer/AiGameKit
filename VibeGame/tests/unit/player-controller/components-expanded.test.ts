import { describe, expect, it } from 'bun:test';
import { MAX_ENTITIES } from '../../../src/core/ecs/constants';
import { ThirdPersonCamera } from '../../../src/plugins/player-controller/components';

const FLOAT_FIELDS = [
  'distance',
  'height',
  'yaw',
  'pitch',
  'positionSmooth',
  'mouseSensitivity',
  'currentX',
  'currentY',
  'currentZ',
  'minTerrainDistance',
  'followX',
  'followY',
  'followZ',
  'smoothYaw',
  'followLag',
  'turnLag',
] as const;

describe('ThirdPersonCamera per-slot storage', () => {
  for (let slot = 0; slot < 30; slot++) {
    it(`float channels round-trip on entity ${slot}`, () => {
      for (const field of FLOAT_FIELDS) {
        const v = slot * 0.11 + field.length * 0.01;
        ThirdPersonCamera[field][slot] = v;
        expect(ThirdPersonCamera[field][slot]).toBeCloseTo(v, 5);
        ThirdPersonCamera[field][slot] = 0;
      }
    });

    it(`target and initialized round-trip on entity ${slot}`, () => {
      ThirdPersonCamera.target[slot] = slot + 500;
      ThirdPersonCamera.initialized[slot] = slot % 2;
      expect(ThirdPersonCamera.target[slot]).toBe(slot + 500);
      expect(ThirdPersonCamera.initialized[slot]).toBe(slot % 2);
      ThirdPersonCamera.target[slot] = 0;
      ThirdPersonCamera.initialized[slot] = 0;
    });
  }

  for (const field of FLOAT_FIELDS) {
    it(`${field} uses Float32Array(MAX_ENTITIES)`, () => {
      expect(ThirdPersonCamera[field]).toBeInstanceOf(Float32Array);
      expect(ThirdPersonCamera[field].length).toBe(MAX_ENTITIES);
    });
  }
});

describe('ThirdPersonCamera recipe wiring', () => {
  for (let i = 0; i < 15; i++) {
    it(`ThirdPersonCameraPlugin exposes merge recipe (check ${i})`, async () => {
      const { ThirdPersonCameraPlugin } =
        await import('../../../src/plugins/player-controller/plugin');
      expect(ThirdPersonCameraPlugin.recipes![0].merge).toBe(true);
    });
  }
});
