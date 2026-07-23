import { describe, expect, it } from 'bun:test';
import {
  PLAYER_BODY_DEFAULTS,
  PLAYER_COLLIDER_DEFAULTS,
  PlayerController,
  PlayerGltfConfig,
} from '../../../src/plugins/player';
import {
  calculateTangentialVelocity,
  processInput,
  JUMP_CONSTANTS,
} from '../../../src/plugins/player/utils';

describe('player bulk: processInput camera yaw', () => {
  for (let deg = 0; deg < 360; deg += 15) {
    it(`forward-only input at yaw ${deg}° is unit length`, () => {
      const yaw = (deg * Math.PI) / 180;
      const v = processInput(1, 0, yaw);
      expect(v.length()).toBeCloseTo(1, 5);
    });
    it(`strafe-only input at yaw ${deg}° is unit length`, () => {
      const yaw = (deg * Math.PI) / 180;
      const v = processInput(0, 1, yaw);
      expect(v.length()).toBeCloseTo(1, 5);
    });
  }
});

describe('player bulk: processInput zero input', () => {
  for (let yawIdx = 0; yawIdx < 20; yawIdx++) {
    it(`zero input yields zero vector run ${yawIdx}`, () => {
      const v = processInput(0, 0, yawIdx);
      expect(v.length()).toBeCloseTo(0, 8);
    });
  }
});

describe('player bulk: calculateTangentialVelocity', () => {
  for (let i = 0; i < 25; i++) {
    it(`cross product case ${i}`, () => {
      const ax = i * 0.1;
      const ay = (i + 1) * 0.2;
      const az = (i + 2) * 0.05;
      const ox = 1;
      const oy = 0;
      const oz = i % 3;
      const v = calculateTangentialVelocity(ax, ay, az, ox, oy, oz);
      expect(v.x).toBeCloseTo(ay * oz - az * oy, 5);
      expect(v.y).toBeCloseTo(az * ox - ax * oz, 5);
      expect(v.z).toBeCloseTo(ax * oy - ay * ox, 5);
    });
  }
});

describe('player bulk: PlayerController SOA', () => {
  const FLOAT_FIELDS = [
    'speed',
    'sprintMultiplier',
    'jumpHeight',
    'rotationSpeed',
    'jumpCooldown',
    'lastGroundedTime',
    'jumpBufferTime',
    'inheritedVelX',
    'inheritedVelZ',
  ] as const;

  for (let eid = 1; eid <= 8; eid++) {
    for (const field of FLOAT_FIELDS) {
      it(`PlayerController.${field}[${eid}]`, () => {
        const val = eid * 1.25;
        PlayerController[field][eid] = val;
        expect(PlayerController[field][eid]).toBeCloseTo(val);
      });
    }
    it(`PlayerController.canJump[${eid}]`, () => {
      PlayerController.canJump[eid] = 1;
      expect(PlayerController.canJump[eid]).toBe(1);
    });
    it(`PlayerController.cameraEntity[${eid}]`, () => {
      PlayerController.cameraEntity[eid] = eid + 100;
      expect(PlayerController.cameraEntity[eid]).toBe(eid + 100);
    });
  }
});

describe('player bulk: PlayerGltfConfig indices', () => {
  for (let eid = 1; eid <= 10; eid++) {
    it(`PlayerGltfConfig clip indices entity ${eid}`, () => {
      PlayerGltfConfig.idleClipIndex[eid] = eid;
      PlayerGltfConfig.walkClipIndex[eid] = eid + 1;
      PlayerGltfConfig.runClipIndex[eid] = eid + 2;
      expect(PlayerGltfConfig.idleClipIndex[eid]).toBe(eid);
      expect(PlayerGltfConfig.walkClipIndex[eid]).toBe(eid + 1);
      expect(PlayerGltfConfig.runClipIndex[eid]).toBe(eid + 2);
    });
  }
});

describe('player bulk: defaults constants', () => {
  it('JUMP_CONSTANTS cooldown', () => {
    expect(JUMP_CONSTANTS.cooldown).toBeCloseTo(0.2);
  });

  for (const key of Object.keys(PLAYER_BODY_DEFAULTS)) {
    it(`PLAYER_BODY_DEFAULTS.${key} is defined`, () => {
      expect(
        PLAYER_BODY_DEFAULTS[key as keyof typeof PLAYER_BODY_DEFAULTS]
      ).toBeDefined();
    });
  }

  for (const key of Object.keys(PLAYER_COLLIDER_DEFAULTS)) {
    it(`PLAYER_COLLIDER_DEFAULTS.${key} is defined`, () => {
      expect(
        PLAYER_COLLIDER_DEFAULTS[key as keyof typeof PLAYER_COLLIDER_DEFAULTS]
      ).toBeDefined();
    });
  }
});

describe('player bulk: processInput diagonal normalization', () => {
  for (let i = 1; i <= 15; i++) {
    it(`diagonal magnitude ${i}`, () => {
      const v = processInput(i, i, 0);
      expect(v.length()).toBeCloseTo(1, 5);
    });
  }
});

describe('player bulk: tangential zero angular velocity', () => {
  for (let i = 0; i < 10; i++) {
    it(`zero omega yields zero tangential ${i}`, () => {
      const v = calculateTangentialVelocity(0, 0, 0, i, i, i);
      expect(v.length()).toBeCloseTo(0, 8);
    });
  }
});
