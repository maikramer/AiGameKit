import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { PlayerController } from 'vibegame/player';

describe('Player Components', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  describe('Player', () => {
    it('should initialize with default values', () => {
      state.addComponent(entity, PlayerController);

      expect(PlayerController.speed[entity]).toBe(0);
      expect(PlayerController.jumpHeight[entity]).toBe(0);
      expect(PlayerController.rotationSpeed[entity]).toBe(0);
      expect(PlayerController.canJump[entity]).toBe(0);
      expect(PlayerController.isJumping[entity]).toBe(0);
      expect(PlayerController.jumpCooldown[entity]).toBe(0);
      expect(PlayerController.lastGroundedTime[entity]).toBe(0);
      expect(PlayerController.jumpBufferTime[entity]).toBe(0);
    });

    it('should store movement configuration', () => {
      state.addComponent(entity, PlayerController);

      PlayerController.speed[entity] = 5;
      PlayerController.jumpHeight[entity] = 4;
      PlayerController.rotationSpeed[entity] = 10;

      expect(PlayerController.speed[entity]).toBe(5);
      expect(PlayerController.jumpHeight[entity]).toBe(4);
      expect(PlayerController.rotationSpeed[entity]).toBe(10);
    });

    it('should track jump state', () => {
      state.addComponent(entity, PlayerController);

      PlayerController.canJump[entity] = 1;
      PlayerController.isJumping[entity] = 0;

      expect(PlayerController.canJump[entity]).toBe(1);
      expect(PlayerController.isJumping[entity]).toBe(0);

      PlayerController.isJumping[entity] = 1;
      PlayerController.canJump[entity] = 0;

      expect(PlayerController.isJumping[entity]).toBe(1);
      expect(PlayerController.canJump[entity]).toBe(0);
    });

    it('should track jump cooldown', () => {
      state.addComponent(entity, PlayerController);

      PlayerController.jumpCooldown[entity] = 0.2;
      expect(PlayerController.jumpCooldown[entity]).toBeCloseTo(0.2);

      PlayerController.jumpCooldown[entity] = 0;
      expect(PlayerController.jumpCooldown[entity]).toBe(0);
    });

    it('should track last grounded time', () => {
      state.addComponent(entity, PlayerController);

      PlayerController.lastGroundedTime[entity] = 1.5;
      expect(PlayerController.lastGroundedTime[entity]).toBe(1.5);
    });

    it('should track jump buffer time', () => {
      state.addComponent(entity, PlayerController);

      PlayerController.jumpBufferTime[entity] = 0.1;
      expect(PlayerController.jumpBufferTime[entity]).toBeCloseTo(0.1);

      PlayerController.jumpBufferTime[entity] = -10000;
      expect(PlayerController.jumpBufferTime[entity]).toBe(-10000);
    });
  });
});
