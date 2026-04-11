import { beforeEach, describe, expect, it } from 'bun:test';
import { JSDOM } from 'jsdom';
import { State, XMLParser, parseXMLToEntities } from 'vibegame';
import { PlayerController, PlayerPlugin } from 'vibegame/player';
import { InputState, InputPlugin } from 'vibegame/input';
import {
  Rigidbody,
  BodyType,
  CharacterController,
  CharacterMovement,
  Collider,
  ColliderShape,
  PhysicsPlugin,
} from 'vibegame/physics';
import { Transform, TransformsPlugin } from 'vibegame/transforms';
import { Respawn, RespawnPlugin } from 'vibegame/respawn';

describe('Player Recipes and XML', () => {
  let state: State;

  beforeEach(async () => {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    global.DOMParser = dom.window.DOMParser;

    state = new State();
    state.registerPlugin(TransformsPlugin);
    state.registerPlugin(PhysicsPlugin);
    state.registerPlugin(RespawnPlugin);
    state.registerPlugin(InputPlugin);
    state.registerPlugin(PlayerPlugin);

    await state.initializePlugins();
  });

  describe('Basic Player Usage (XML)', () => {
    it('should create player with default values', () => {
      const xml = '<root><Player /></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);
      const player = entities[0].entity;

      expect(state.hasComponent(player, PlayerController)).toBe(true);
      expect(state.hasComponent(player, CharacterMovement)).toBe(true);
      expect(state.hasComponent(player, Transform)).toBe(true);
      expect(state.hasComponent(player, Rigidbody)).toBe(true);
      expect(state.hasComponent(player, Collider)).toBe(true);
      expect(state.hasComponent(player, CharacterController)).toBe(true);
      expect(state.hasComponent(player, InputState)).toBe(true);
      expect(state.hasComponent(player, Respawn)).toBe(true);

      expect(PlayerController.speed[player]).toBeCloseTo(5.3);
      expect(PlayerController.jumpHeight[player]).toBeCloseTo(2.3);
      expect(PlayerController.rotationSpeed[player]).toBe(10);
      expect(PlayerController.canJump[player]).toBe(1);
      expect(PlayerController.isJumping[player]).toBe(0);
      expect(PlayerController.jumpCooldown[player]).toBe(0);
      expect(PlayerController.lastGroundedTime[player]).toBe(0);
      expect(PlayerController.jumpBufferTime[player]).toBe(-10000);
      expect(PlayerController.cameraEntity[player]).toBe(0);
    });

    it('should create player with custom position, speed and jump height', () => {
      const xml =
        '<root><Player pos="0 2 0" speed="6" jump-height="3" /></root>';
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);
      const player = entities[0].entity;

      expect(Transform.posX[player]).toBe(0);
      expect(Transform.posY[player]).toBe(2);
      expect(Transform.posZ[player]).toBe(0);
      expect(PlayerController.speed[player]).toBe(6);
      expect(PlayerController.jumpHeight[player]).toBe(3);
    });
  });

  describe('Custom Player Configuration (XML)', () => {
    it('should create player with all custom attributes', () => {
      const xml = `
        <root>
          <Player
            pos="5 1 -10"
            speed="8"
            jump-height="4"
            rotation-speed="15"
          />
        </root>
      `;
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);
      const player = entities[0].entity;

      expect(Transform.posX[player]).toBe(5);
      expect(Transform.posY[player]).toBe(1);
      expect(Transform.posZ[player]).toBe(-10);
      expect(PlayerController.speed[player]).toBe(8);
      expect(PlayerController.jumpHeight[player]).toBe(4);
      expect(PlayerController.rotationSpeed[player]).toBe(15);
    });

    it('should handle CSS-style syntax for player attributes', () => {
      const xml = `
        <root>
          <Player playerController="speed: 7; jump-height: 3.5" />
        </root>
      `;
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);
      const player = entities[0].entity;

      expect(PlayerController.speed[player]).toBe(7);
      expect(PlayerController.jumpHeight[player]).toBe(3.5);
    });

    it('should handle dot notation for player attributes', () => {
      const xml = `
        <root>
          <Player
            playerController.speed="10"
            playerController.jump-height="5"
            playerController.rotation-speed="12"
          />
        </root>
      `;
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);
      const player = entities[0].entity;

      expect(PlayerController.speed[player]).toBe(10);
      expect(PlayerController.jumpHeight[player]).toBe(5);
      expect(PlayerController.rotationSpeed[player]).toBe(12);
    });
  });

  describe('Player Recipe Defaults', () => {
    it('should have correct default body configuration', () => {
      const entity = state.createFromRecipe('Player');

      expect(Rigidbody.type[entity]).toBe(BodyType.KinematicPositionBased);
      expect(Rigidbody.mass[entity]).toBe(1);
      expect(Rigidbody.linearDamping[entity]).toBe(0);
      expect(Rigidbody.angularDamping[entity]).toBe(0);
      expect(Rigidbody.gravityScale[entity]).toBe(1);
      expect(Rigidbody.ccd[entity]).toBe(1);
      expect(Rigidbody.lockRotX[entity]).toBe(1);
      expect(Rigidbody.lockRotY[entity]).toBe(0);
      expect(Rigidbody.lockRotZ[entity]).toBe(1);
    });

    it('should have correct default collider configuration', () => {
      const entity = state.createFromRecipe('Player');

      expect(Collider.shape[entity]).toBe(ColliderShape.Capsule);
      expect(Collider.radius[entity]).toBeCloseTo(0.3);
      expect(Collider.height[entity]).toBeCloseTo(0.9);
      expect(Collider.friction[entity]).toBe(0);
      expect(Collider.restitution[entity]).toBe(0);
      expect(Collider.density[entity]).toBe(1);
      expect(Collider.posOffsetY[entity]).toBeCloseTo(0.75);
    });

    it('should have correct default character controller configuration', () => {
      const entity = state.createFromRecipe('Player');

      expect(CharacterController.offset[entity]).toBeCloseTo(0.08);
      expect(CharacterController.maxSlope[entity]).toBeCloseTo(Math.PI / 4);
      expect(CharacterController.maxSlide[entity]).toBeCloseTo(
        30 * (Math.PI / 180)
      );
      expect(CharacterController.snapDist[entity]).toBeCloseTo(0.5);
      expect(CharacterController.autoStep[entity]).toBe(1);
      expect(CharacterController.maxStepHeight[entity]).toBeCloseTo(0.3);
      expect(CharacterController.minStepWidth[entity]).toBeCloseTo(0.05);
    });

    it('should have correct default transform values', () => {
      const entity = state.createFromRecipe('Player');

      expect(Transform.posX[entity]).toBe(0);
      expect(Transform.posY[entity]).toBe(0);
      expect(Transform.posZ[entity]).toBe(0);
      expect(Transform.rotX[entity]).toBe(0);
      expect(Transform.rotY[entity]).toBe(0);
      expect(Transform.rotZ[entity]).toBe(0);
      expect(Transform.rotW[entity]).toBe(1);
      expect(Transform.scaleX[entity]).toBe(1);
      expect(Transform.scaleY[entity]).toBe(1);
      expect(Transform.scaleZ[entity]).toBe(1);
    });

    it('should have correct default respawn values', () => {
      const entity = state.createFromRecipe('Player');

      expect(Respawn.posX[entity]).toBe(0);
      expect(Respawn.posY[entity]).toBe(0);
      expect(Respawn.posZ[entity]).toBe(0);
      expect(Respawn.eulerX[entity]).toBe(0);
      expect(Respawn.eulerY[entity]).toBe(0);
      expect(Respawn.eulerZ[entity]).toBe(0);
    });
  });

  describe('Creating Player Programmatically', () => {
    it('should create player entity with recipe components', () => {
      const player = state.createEntity();

      state.addComponent(player, PlayerController, {
        speed: 7,
        jumpHeight: 3.5,
      });

      state.addComponent(player, Transform, { posY: 5 });
      state.addComponent(player, Rigidbody, {
        type: BodyType.KinematicPositionBased,
      });
      state.addComponent(player, CharacterController);
      state.addComponent(player, InputState);

      expect(state.hasComponent(player, PlayerController)).toBe(true);
      expect(state.hasComponent(player, Transform)).toBe(true);
      expect(state.hasComponent(player, Rigidbody)).toBe(true);
      expect(state.hasComponent(player, CharacterController)).toBe(true);
      expect(state.hasComponent(player, InputState)).toBe(true);

      expect(PlayerController.speed[player]).toBe(7);
      expect(PlayerController.jumpHeight[player]).toBe(3.5);
      expect(Transform.posY[player]).toBe(5);
      expect(Rigidbody.type[player]).toBe(BodyType.KinematicPositionBased);
    });

    it('should handle missing components gracefully', () => {
      const player = state.createEntity();

      state.addComponent(player, PlayerController);
      state.addComponent(player, Transform);

      expect(state.hasComponent(player, PlayerController)).toBe(true);
      expect(state.hasComponent(player, Transform)).toBe(true);
      expect(state.hasComponent(player, Rigidbody)).toBe(false);
      expect(state.hasComponent(player, CharacterController)).toBe(false);
    });
  });

  describe('Player Recipe with Overrides', () => {
    it('should override default values using recipe', () => {
      const entity = state.createFromRecipe('Player', {
        'playerController.speed': 12,
        'playerController.jump-height': 6,
        'playerController.rotation-speed': 20,
        'transform.pos-y': 10,
        'rigidbody.gravity-scale': 0.5,
      });

      expect(PlayerController.speed[entity]).toBe(12);
      expect(PlayerController.jumpHeight[entity]).toBe(6);
      expect(PlayerController.rotationSpeed[entity]).toBe(20);
      expect(Transform.posY[entity]).toBe(10);
      expect(Rigidbody.gravityScale[entity]).toBe(0.5);
    });

    it('should handle transform position override', () => {
      const entity = state.createFromRecipe('Player', {
        'transform.pos-x': 5,
        'transform.pos-y': 10,
        'transform.pos-z': -3,
      });

      expect(Transform.posX[entity]).toBe(5);
      expect(Transform.posY[entity]).toBe(10);
      expect(Transform.posZ[entity]).toBe(-3);
    });

    it('should handle collider customization', () => {
      const entity = state.createFromRecipe('Player', {
        'collider.radius': 0.5,
        'collider.height': 1.5,
        'collider.friction': 0.3,
      });

      expect(Collider.radius[entity]).toBeCloseTo(0.5);
      expect(Collider.height[entity]).toBeCloseTo(1.5);
      expect(Collider.friction[entity]).toBeCloseTo(0.3);
    });
  });

  describe('Multiple Players', () => {
    it('should handle multiple players with different configurations', () => {
      const xml = `
        <root>
          <Player pos="0 0 0" speed="5" />
          <Player pos="10 0 10" speed="10" jump-height="5" />
        </root>
      `;
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(entities.length).toBe(2);

      const player1 = entities[0].entity;
      const player2 = entities[1].entity;

      expect(Transform.posX[player1]).toBe(0);
      expect(Transform.posZ[player1]).toBe(0);
      expect(PlayerController.speed[player1]).toBe(5);
      expect(PlayerController.jumpHeight[player1]).toBeCloseTo(2.3);

      expect(Transform.posX[player2]).toBe(10);
      expect(Transform.posZ[player2]).toBe(10);
      expect(PlayerController.speed[player2]).toBe(10);
      expect(PlayerController.jumpHeight[player2]).toBe(5);
    });
  });

  describe('Nested Player Entities', () => {
    it('should handle player as child entity', () => {
      const xml = `
        <root>
          <GameObject>
            <Player speed="7" />
          </GameObject>
        </root>
      `;
      const parsed = XMLParser.parse(xml);
      const entities = parseXMLToEntities(state, parsed.root);

      expect(entities.length).toBe(1);
      expect(entities[0].children.length).toBe(1);

      const player = entities[0].children[0].entity;
      expect(state.hasComponent(player, PlayerController)).toBe(true);
      expect(PlayerController.speed[player]).toBe(7);
    });
  });
});
