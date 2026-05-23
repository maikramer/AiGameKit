import { beforeAll, beforeEach, describe, expect, it } from 'bun:test';
import { FollowCamera } from '../../../../src/plugins/follow-camera/components';
import { PlayerController } from '../../../../src/plugins/player/components';
import {
  resolveMouseMode,
  updateRotation,
} from '../../../../src/plugins/player/utils';

function yawFromQuat(q: { x: number; y: number; z: number; w: number }): number {
  const sinY = 2 * (q.w * q.y - q.z * q.x);
  const cosY = 1 - 2 * (q.y * q.y + q.z * q.z);
  return Math.atan2(sinY, cosY);
}

function makeIdentityRot() {
  return { rotX: 0, rotY: 0, rotZ: 0, rotW: 1 };
}

function makeRotFromYaw(yaw: number) {
  return {
    rotX: 0,
    rotY: Math.sin(yaw / 2),
    rotZ: 0,
    rotW: Math.cos(yaw / 2),
  };
}

describe('updateRotation', () => {
  let entity: number;
  let camEntity: number;
  const deltaTime = 0.016;
  const rotationSpeed = 10;

  function makeWorld() {
    return {} as any;
  }

  beforeEach(() => {
    entity = 1;
    camEntity = 2;

    PlayerController.cameraEntity[entity] = camEntity;
    PlayerController.rotationSpeed[entity] = rotationSpeed;

    FollowCamera.mouseMode[camEntity] = 1;
  });

  describe('mode 0: face camera when idle, face movement when walking', () => {
    beforeEach(() => {
      FollowCamera.mouseMode[camEntity] = 0;
    });

    it('should slerp toward camera yaw when idle', () => {
      const cameraYaw = Math.PI / 4;
      const inputVector = { x: 0, y: 0, z: 0, length: () => 0 } as any;

      const result = updateRotation(
        entity,
        inputVector,
        deltaTime,
        makeIdentityRot(),
        cameraYaw,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(resultYaw).not.toBe(0);
      const diff = Math.abs(resultYaw - cameraYaw);
      expect(diff).toBeGreaterThan(0);
    });

    it('should slerp toward movement direction when moving', () => {
      const cameraYaw = Math.PI / 4;
      const moveAngle = Math.PI / 2;
      const inputVector = {
        x: Math.sin(moveAngle),
        y: 0,
        z: Math.cos(moveAngle),
        length: () => 1,
      } as any;

      const result = updateRotation(
        entity,
        inputVector,
        deltaTime,
        makeIdentityRot(),
        cameraYaw,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(Math.abs(resultYaw - moveAngle)).toBeGreaterThan(0);
      expect(Math.abs(resultYaw)).toBeLessThan(Math.PI);
    });
  });

  describe('mode 1: only rotate when moving', () => {
    beforeEach(() => {
      FollowCamera.mouseMode[camEntity] = 1;
    });

    it('should return unchanged rotation when idle', () => {
      const inputVector = { x: 0, y: 0, z: 0, length: () => 0 } as any;
      const rot = makeRotFromYaw(1.0);

      const result = updateRotation(
        entity,
        inputVector,
        deltaTime,
        rot,
        0,
        makeWorld(),
      );

      expect(result.x).toBeCloseTo(rot.rotX, 5);
      expect(result.y).toBeCloseTo(rot.rotY, 5);
      expect(result.z).toBeCloseTo(rot.rotZ, 5);
      expect(result.w).toBeCloseTo(rot.rotW, 5);
    });

    it('should slerp toward movement direction when moving', () => {
      const moveAngle = Math.PI;
      const inputVector = {
        x: Math.sin(moveAngle),
        y: 0,
        z: Math.cos(moveAngle),
        length: () => 1,
      } as any;

      const result = updateRotation(
        entity,
        inputVector,
        deltaTime,
        makeIdentityRot(),
        0,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(Math.abs(resultYaw)).toBeGreaterThan(0);
    });
  });

  describe('mode 4: instant snap to camera direction', () => {
    beforeEach(() => {
      FollowCamera.mouseMode[camEntity] = 4;
    });

    it('should instantly face camera direction (no slerp)', () => {
      const cameraYaw = Math.PI / 3;

      const result = updateRotation(
        entity,
        { x: 0, y: 0, z: 0, length: () => 0 } as any,
        deltaTime,
        makeIdentityRot(),
        cameraYaw,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(resultYaw).toBeCloseTo(cameraYaw, 5);
    });

    it('should snap to camera yaw even when moving', () => {
      const cameraYaw = -Math.PI / 4;
      const inputVector = {
        x: 1,
        y: 0,
        z: 0,
        length: () => 1,
      } as any;

      const result = updateRotation(
        entity,
        inputVector,
        deltaTime,
        makeIdentityRot(),
        cameraYaw,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(resultYaw).toBeCloseTo(cameraYaw, 5);
    });

    it('should snap to camera yaw even when moving', () => {
      const cameraYaw = -Math.PI / 4;
      const inputVector = {
        x: 1,
        y: 0,
        z: 0,
        length: () => 1,
      } as any;

      const result = updateRotation(
        entity,
        inputVector,
        deltaTime,
        makeIdentityRot(),
        cameraYaw,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(resultYaw).toBeCloseTo(cameraYaw, 5);
    });
  });

  describe('resolveMouseMode', () => {
    it('should return mode from FollowCamera.mouseMode', () => {
      FollowCamera.mouseMode[camEntity] = 0;
      expect(resolveMouseMode(entity, makeWorld())).toBe(0);

      FollowCamera.mouseMode[camEntity] = 1;
      expect(resolveMouseMode(entity, makeWorld())).toBe(1);

      FollowCamera.mouseMode[camEntity] = 4;
      expect(resolveMouseMode(entity, makeWorld())).toBe(4);
    });

    it('should return 1 when camera entity is 0 (no camera)', () => {
      PlayerController.cameraEntity[entity] = 0;
      expect(resolveMouseMode(entity, makeWorld())).toBe(1);
    });

    it('should clamp out-of-range modes to 1', () => {
      FollowCamera.mouseMode[camEntity] = 99;
      expect(resolveMouseMode(entity, makeWorld())).toBe(1);
    });
  });

  describe('mode 2: same behavior as mode 0', () => {
    beforeEach(() => {
      FollowCamera.mouseMode[camEntity] = 2;
    });

    it('should face camera when idle', () => {
      const cameraYaw = Math.PI / 6;

      const result = updateRotation(
        entity,
        { x: 0, y: 0, z: 0, length: () => 0 } as any,
        deltaTime,
        makeIdentityRot(),
        cameraYaw,
        makeWorld(),
      );

      const resultYaw = yawFromQuat(result);
      expect(Math.abs(resultYaw - cameraYaw)).toBeGreaterThan(0);
    });
  });

  describe('mode 3: same behavior as mode 1', () => {
    beforeEach(() => {
      FollowCamera.mouseMode[camEntity] = 3;
    });

    it('should keep rotation unchanged when idle', () => {
      const rot = makeRotFromYaw(0.8);

      const result = updateRotation(
        entity,
        { x: 0, y: 0, z: 0, length: () => 0 } as any,
        deltaTime,
        rot,
        0,
        makeWorld(),
      );

      expect(result.x).toBeCloseTo(rot.rotX, 5);
      expect(result.y).toBeCloseTo(rot.rotY, 5);
      expect(result.z).toBeCloseTo(rot.rotZ, 5);
      expect(result.w).toBeCloseTo(rot.rotW, 5);
    });
  });
});
