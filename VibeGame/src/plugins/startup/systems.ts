import { defineQuery, Parent, type System } from '../../core';
import { AnimatedCharacter } from '../animation';
import { HasAnimator } from '../animation/components';
import { InputState } from '../input';
import { OrbitCamera } from '../orbit-camera';
import {
  Rigidbody,
  CharacterController,
  CharacterMovement,
  Collider,
} from '../physics';
import {
  PlayerController,
  PLAYER_BODY_DEFAULTS,
  PLAYER_COLLIDER_DEFAULTS,
} from '../player';
import { AmbientLight, DirectionalLight, MainCamera } from '../rendering';
import { Respawn } from '../respawn';
import { Transform } from '../transforms';

const ambientQuery = defineQuery([AmbientLight]);
const directionalQuery = defineQuery([DirectionalLight]);
const playersQuery = defineQuery([PlayerController]);
const mainCameraQuery = defineQuery([MainCamera]);
const playersWithoutAnimatorQuery = defineQuery([PlayerController]);

export const LightingStartupSystem: System = {
  group: 'setup',
  update: (state) => {
    const existingHemisphereLight = ambientQuery(state.world);
    const existingDirectionalLight = directionalQuery(state.world);

    if (
      existingHemisphereLight.length === 0 &&
      existingDirectionalLight.length === 0
    ) {
      const light = state.createEntity();
      state.addComponent(light, DirectionalLight);
      state.addComponent(light, AmbientLight);
    }
  },
};

export const PlayerStartupSystem: System = {
  group: 'setup',
  update: (state) => {
    const existingPlayers = playersQuery(state.world);
    if (existingPlayers.length === 0) {
      const entity = state.createEntity();

      state.addComponent(entity, PlayerController);
      state.addComponent(entity, CharacterMovement);
      state.addComponent(entity, Transform);

      state.addComponent(entity, Rigidbody);
      Rigidbody.type[entity] = PLAYER_BODY_DEFAULTS.type;
      Rigidbody.mass[entity] = PLAYER_BODY_DEFAULTS.mass;
      Rigidbody.posX[entity] = PLAYER_BODY_DEFAULTS.posX;
      Rigidbody.posY[entity] = PLAYER_BODY_DEFAULTS.posY;
      Rigidbody.posZ[entity] = PLAYER_BODY_DEFAULTS.posZ;
      Rigidbody.eulerX[entity] = PLAYER_BODY_DEFAULTS.eulerX;
      Rigidbody.eulerY[entity] = PLAYER_BODY_DEFAULTS.eulerY;
      Rigidbody.eulerZ[entity] = PLAYER_BODY_DEFAULTS.eulerZ;
      Rigidbody.rotX[entity] = 0;
      Rigidbody.rotY[entity] = 0;
      Rigidbody.rotZ[entity] = 0;
      Rigidbody.rotW[entity] = 1;
      Rigidbody.velX[entity] = PLAYER_BODY_DEFAULTS.velX;
      Rigidbody.velY[entity] = PLAYER_BODY_DEFAULTS.velY;
      Rigidbody.velZ[entity] = PLAYER_BODY_DEFAULTS.velZ;
      Rigidbody.rotVelX[entity] = PLAYER_BODY_DEFAULTS.rotVelX;
      Rigidbody.rotVelY[entity] = PLAYER_BODY_DEFAULTS.rotVelY;
      Rigidbody.rotVelZ[entity] = PLAYER_BODY_DEFAULTS.rotVelZ;
      Rigidbody.linearDamping[entity] = PLAYER_BODY_DEFAULTS.linearDamping;
      Rigidbody.angularDamping[entity] = PLAYER_BODY_DEFAULTS.angularDamping;
      Rigidbody.gravityScale[entity] = PLAYER_BODY_DEFAULTS.gravityScale;
      Rigidbody.ccd[entity] = PLAYER_BODY_DEFAULTS.ccd;
      Rigidbody.lockRotX[entity] = PLAYER_BODY_DEFAULTS.lockRotX;
      Rigidbody.lockRotY[entity] = PLAYER_BODY_DEFAULTS.lockRotY;
      Rigidbody.lockRotZ[entity] = PLAYER_BODY_DEFAULTS.lockRotZ;

      state.addComponent(entity, Collider);
      Collider.shape[entity] = PLAYER_COLLIDER_DEFAULTS.shape;
      Collider.radius[entity] = PLAYER_COLLIDER_DEFAULTS.radius;
      Collider.height[entity] = PLAYER_COLLIDER_DEFAULTS.height;
      Collider.sizeX[entity] = PLAYER_COLLIDER_DEFAULTS.sizeX;
      Collider.sizeY[entity] = PLAYER_COLLIDER_DEFAULTS.sizeY;
      Collider.sizeZ[entity] = PLAYER_COLLIDER_DEFAULTS.sizeZ;
      Collider.friction[entity] = PLAYER_COLLIDER_DEFAULTS.friction;
      Collider.restitution[entity] = PLAYER_COLLIDER_DEFAULTS.restitution;
      Collider.density[entity] = PLAYER_COLLIDER_DEFAULTS.density;
      Collider.isSensor[entity] = PLAYER_COLLIDER_DEFAULTS.isSensor;
      Collider.membershipGroups[entity] =
        PLAYER_COLLIDER_DEFAULTS.membershipGroups;
      Collider.filterGroups[entity] = PLAYER_COLLIDER_DEFAULTS.filterGroups;
      Collider.posOffsetX[entity] = PLAYER_COLLIDER_DEFAULTS.posOffsetX;
      Collider.posOffsetY[entity] = PLAYER_COLLIDER_DEFAULTS.posOffsetY;
      Collider.posOffsetZ[entity] = PLAYER_COLLIDER_DEFAULTS.posOffsetZ;
      Collider.rotOffsetX[entity] = PLAYER_COLLIDER_DEFAULTS.rotOffsetX;
      Collider.rotOffsetY[entity] = PLAYER_COLLIDER_DEFAULTS.rotOffsetY;
      Collider.rotOffsetZ[entity] = PLAYER_COLLIDER_DEFAULTS.rotOffsetZ;
      Collider.rotOffsetW[entity] = PLAYER_COLLIDER_DEFAULTS.rotOffsetW;

      state.addComponent(entity, CharacterController);
      state.addComponent(entity, InputState);
      state.addComponent(entity, Respawn);
    }
  },
};

export const CameraStartupSystem: System = {
  group: 'setup',
  update: (state) => {
    const existingCameras = mainCameraQuery(state.world);
    if (existingCameras.length === 0) {
      const camera = state.createEntity();
      state.addComponent(camera, OrbitCamera);
      state.addComponent(camera, Transform);
      state.addComponent(camera, MainCamera);
      state.addComponent(camera, InputState);
      OrbitCamera.inputSource[camera] = camera;
    }
  },
};

export const PlayerCharacterSystem: System = {
  group: 'setup',
  update(state) {
    const playersWithoutCharacter = playersWithoutAnimatorQuery(
      state.world
    ).filter((entity) => !state.hasComponent(entity, HasAnimator));

    for (const player of playersWithoutCharacter) {
      const character = state.createEntity();
      state.addComponent(player, HasAnimator);
      state.addComponent(character, Transform);
      state.addComponent(character, Parent);
      state.addComponent(character, AnimatedCharacter);
      Transform.posY[character] = 0.75;
      Parent.entity[character] = player;
    }
  },
};
