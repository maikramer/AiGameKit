import { beforeEach, describe, expect, it } from 'bun:test';
import { State, TIME_CONSTANTS, defineQuery } from 'vibegame';
import {
  Rigidbody,
  BodyType,
  CharacterController,
  CharacterMovement,
  Collider,
  ColliderShape,
  PhysicsPlugin,
} from 'vibegame/physics';
import { InputState } from 'vibegame/input';
import { OrbitCamera, OrbitCameraPlugin } from 'vibegame/orbit-camera';
import { PlayerController, PlayerPlugin } from 'vibegame/player';
import {
  Transform,
  TransformsPlugin,
  WorldTransform,
} from 'vibegame/transforms';

describe('Player Movement', () => {
  let state: State;

  beforeEach(async () => {
    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(PhysicsPlugin);
    state.registerPlugin(OrbitCameraPlugin);
    state.registerPlugin(PlayerPlugin);

    await state.initializePlugins();
  });

  function createPlayer(x = 0, y = 5, z = 0): number {
    const player = state.createEntity();
    state.addComponent(player, PlayerController);
    state.addComponent(player, InputState);
    state.addComponent(player, Rigidbody);
    state.addComponent(player, Collider);
    state.addComponent(player, CharacterController);
    state.addComponent(player, CharacterMovement);
    state.addComponent(player, Transform);
    state.addComponent(player, WorldTransform);

    PlayerController.speed[player] = 10;
    PlayerController.jumpHeight[player] = 3;
    PlayerController.rotationSpeed[player] = 10;
    PlayerController.canJump[player] = 1;
    PlayerController.isJumping[player] = 0;
    PlayerController.jumpCooldown[player] = 0;
    PlayerController.lastGroundedTime[player] = 0;
    PlayerController.jumpBufferTime[player] = -10000;

    Rigidbody.type[player] = BodyType.KinematicPositionBased;
    Rigidbody.posX[player] = x;
    Rigidbody.posY[player] = y;
    Rigidbody.posZ[player] = z;
    Rigidbody.gravityScale[player] = 1;
    Rigidbody.rotW[player] = 1;

    Collider.shape[player] = ColliderShape.Capsule;
    Collider.radius[player] = 0.5;
    Collider.height[player] = 1;

    CharacterController.offset[player] = 0.01;
    CharacterController.upY[player] = 1;

    CharacterMovement.velocityY[player] = 0;

    Transform.posX[player] = x;
    Transform.posY[player] = y;
    Transform.posZ[player] = z;
    Transform.rotW[player] = 1;

    return player;
  }

  function createFloor(): number {
    const floor = state.createEntity();
    state.addComponent(floor, Rigidbody);
    state.addComponent(floor, Collider);
    state.addComponent(floor, Transform);

    Rigidbody.type[floor] = BodyType.Fixed;
    Rigidbody.posY[floor] = 0;
    Rigidbody.rotW[floor] = 1;

    Collider.shape[floor] = ColliderShape.Box;
    Collider.sizeX[floor] = 100;
    Collider.sizeY[floor] = 1;
    Collider.sizeZ[floor] = 100;

    return floor;
  }

  function createCamera(): number {
    const camera = state.createEntity();
    state.addComponent(camera, OrbitCamera);
    OrbitCamera.currentYaw[camera] = 0;
    return camera;
  }

  describe('Basic Movement', () => {
    it('should move forward based on input', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      InputState.moveY[player] = 1;
      InputState.moveX[player] = 0;

      const initialZ = Rigidbody.posZ[player];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.posZ[player]).toBeLessThan(initialZ);
      expect(Math.abs(Rigidbody.posZ[player] - initialZ)).toBeGreaterThan(0.5);
    });

    it('should move sideways based on input', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      InputState.moveY[player] = 0;
      InputState.moveX[player] = 1;

      const initialX = Rigidbody.posX[player];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.posX[player]).toBeGreaterThan(initialX);
      expect(Math.abs(Rigidbody.posX[player] - initialX)).toBeGreaterThan(0.5);
    });

    it('should normalize diagonal movement', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      InputState.moveY[player] = 1;
      InputState.moveX[player] = 1;

      const initialX = Rigidbody.posX[player];
      const initialZ = Rigidbody.posZ[player];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      const deltaX = Rigidbody.posX[player] - initialX;
      const deltaZ = Rigidbody.posZ[player] - initialZ;
      const totalDistance = Math.sqrt(deltaX * deltaX + deltaZ * deltaZ);

      const forwardOnlyPlayer = createPlayer(10, 2, 0);
      InputState.moveY[forwardOnlyPlayer] = 1;
      InputState.moveX[forwardOnlyPlayer] = 0;

      const forwardInitialZ = Rigidbody.posZ[forwardOnlyPlayer];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      const forwardDistance = Math.abs(
        Rigidbody.posZ[forwardOnlyPlayer] - forwardInitialZ
      );

      expect(totalDistance).toBeCloseTo(forwardDistance, 0);
    });

    it('should respect player speed setting', () => {
      createFloor();
      const slowPlayer = createPlayer(0, 2, 0);
      const fastPlayer = createPlayer(10, 2, 0);
      createCamera();

      PlayerController.speed[slowPlayer] = 5;
      PlayerController.speed[fastPlayer] = 15;

      InputState.moveY[slowPlayer] = 1;
      InputState.moveY[fastPlayer] = 1;

      const slowInitialZ = Rigidbody.posZ[slowPlayer];
      const fastInitialZ = Rigidbody.posZ[fastPlayer];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      const slowDistance = Math.abs(Rigidbody.posZ[slowPlayer] - slowInitialZ);
      const fastDistance = Math.abs(Rigidbody.posZ[fastPlayer] - fastInitialZ);

      expect(fastDistance).toBeGreaterThan(slowDistance * 1.5);
      expect(fastDistance).toBeLessThan(slowDistance * 3.5);
    });
  });

  describe('Camera-Relative Movement', () => {
    it('should move relative to camera yaw', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      const camera = createCamera();

      OrbitCamera.currentYaw[camera] = Math.PI / 2;

      InputState.moveY[player] = 1;
      InputState.moveX[player] = 0;

      const initialX = Rigidbody.posX[player];
      const initialZ = Rigidbody.posZ[player];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.posX[player]).toBeLessThan(initialX - 0.5);
      expect(Math.abs(Rigidbody.posZ[player] - initialZ)).toBeLessThan(0.5);
    });

    it('should handle full 360 degree camera rotation', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      const camera = createCamera();

      const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
      const expectedDirections = [
        { x: 0, z: -1 },
        { x: -1, z: 0 },
        { x: 0, z: 1 },
        { x: 1, z: 0 },
      ];

      for (let i = 0; i < angles.length; i++) {
        Rigidbody.posX[player] = 0;
        Rigidbody.posZ[player] = 0;
        OrbitCamera.currentYaw[camera] = angles[i];

        InputState.moveY[player] = 1;
        InputState.moveX[player] = 0;

        for (let j = 0; j < 10; j++) {
          state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
        }

        const deltaX = Rigidbody.posX[player];
        const deltaZ = Rigidbody.posZ[player];

        const normalizedX =
          deltaX / Math.sqrt(deltaX * deltaX + deltaZ * deltaZ);
        const normalizedZ =
          deltaZ / Math.sqrt(deltaX * deltaX + deltaZ * deltaZ);

        expect(normalizedX).toBeCloseTo(expectedDirections[i].x, 0);
        expect(normalizedZ).toBeCloseTo(expectedDirections[i].z, 0);
      }
    });
  });

  describe('Player Rotation', () => {
    it('should rotate toward movement direction', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      InputState.moveY[player] = 0;
      InputState.moveX[player] = 1;

      for (let i = 0; i < 20; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      const quatY = Rigidbody.rotY[player];
      expect(Math.abs(quatY)).toBeGreaterThan(0.1);
    });

    it('should smoothly interpolate rotation', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      PlayerController.rotationSpeed[player] = 5;

      InputState.moveY[player] = 0;
      InputState.moveX[player] = 1;

      const rotationSteps: number[] = [];

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
        rotationSteps.push(Rigidbody.rotY[player]);
      }

      for (let i = 1; i < rotationSteps.length; i++) {
        const delta = Math.abs(rotationSteps[i] - rotationSteps[i - 1]);
        expect(delta).toBeLessThan(0.2);
      }
    });

    it('should not rotate when stationary', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      Rigidbody.rotY[player] = 0.5;
      const initialRotY = Rigidbody.rotY[player];

      InputState.moveY[player] = 0;
      InputState.moveX[player] = 0;

      for (let i = 0; i < 10; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.rotY[player]).toBeCloseTo(initialRotY, 3);
    });
  });

  describe('Collision Handling', () => {
    it('should stop at walls', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      const wall = state.createEntity();
      state.addComponent(wall, Rigidbody);
      state.addComponent(wall, Collider);
      state.addComponent(wall, Transform);

      Rigidbody.type[wall] = BodyType.Fixed;
      Rigidbody.posX[wall] = 0;
      Rigidbody.posY[wall] = 2;
      Rigidbody.posZ[wall] = -3;
      Rigidbody.rotW[wall] = 1;

      Collider.shape[wall] = ColliderShape.Box;
      Collider.sizeX[wall] = 10;
      Collider.sizeY[wall] = 4;
      Collider.sizeZ[wall] = 1;

      InputState.moveY[player] = 1;

      for (let i = 0; i < 50; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.posZ[player]).toBeGreaterThan(-2.5);
    });

    it('should slide along walls', () => {
      createFloor();
      const player = createPlayer(0, 2, 0);
      createCamera();

      const wall = state.createEntity();
      state.addComponent(wall, Rigidbody);
      state.addComponent(wall, Collider);
      state.addComponent(wall, Transform);

      Rigidbody.type[wall] = BodyType.Fixed;
      Rigidbody.posX[wall] = 0;
      Rigidbody.posY[wall] = 2;
      Rigidbody.posZ[wall] = -2;
      Rigidbody.rotW[wall] = 1;

      Collider.shape[wall] = ColliderShape.Box;
      Collider.sizeX[wall] = 10;
      Collider.sizeY[wall] = 4;
      Collider.sizeZ[wall] = 1;

      InputState.moveY[player] = 1;
      InputState.moveX[player] = 1;

      for (let i = 0; i < 30; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(Rigidbody.posX[player]).toBeGreaterThan(0.5);
      expect(Rigidbody.posZ[player]).toBeGreaterThan(-1.5);
    });
  });

  describe('Ground Detection', () => {
    it('should detect when grounded', () => {
      createFloor();
      const player = createPlayer(0, 1.5, 0);
      createCamera();

      for (let i = 0; i < 30; i++) {
        state.step(TIME_CONSTANTS.FIXED_TIMESTEP);
      }

      expect(CharacterController.grounded[player]).toBe(1);
    });

    it('should detect when airborne', () => {
      const player = createPlayer(0, 10, 0);
      createCamera();

      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      expect(CharacterController.grounded[player]).toBe(0);
    });
  });

  describe('Accessing Player Component in Systems', () => {
    it('should allow checking and modifying player state in systems', () => {
      createFloor();
      const player = createPlayer(0, 10, 0);
      createCamera();

      let jumpDetected = false;
      let speedModified = false;

      const MySystem = {
        update: (state: State) => {
          const players = defineQuery([PlayerController])(state.world);
          for (const entity of players) {
            if (PlayerController.isJumping[entity]) {
              jumpDetected = true;
            }

            PlayerController.speed[entity] = 10;
            speedModified = true;
          }
        },
      };

      PlayerController.speed[player] = 5;
      expect(PlayerController.speed[player]).toBe(5);

      MySystem.update(state);
      expect(speedModified).toBe(true);
      expect(PlayerController.speed[player]).toBe(10);

      InputState.jump[player] = 1;
      state.step(TIME_CONSTANTS.FIXED_TIMESTEP);

      PlayerController.isJumping[player] = 1;
      MySystem.update(state);
      expect(jumpDetected).toBe(true);
    });

    it('should handle multiple players in system queries', () => {
      createFloor();
      createPlayer(0, 2, 0);
      createPlayer(5, 2, 5);
      createCamera();

      let playerCount = 0;
      const speeds: number[] = [];

      const CountingSystem = {
        update: (state: State) => {
          const players = defineQuery([PlayerController])(state.world);
          playerCount = players.length;
          for (const entity of players) {
            speeds.push(PlayerController.speed[entity]);
          }
        },
      };

      CountingSystem.update(state);
      expect(playerCount).toBe(2);
      expect(speeds).toEqual([10, 10]);
    });
  });
});
