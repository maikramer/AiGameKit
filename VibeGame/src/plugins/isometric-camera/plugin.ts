import type { Plugin } from '../../core';
import { IsometricCamera } from './components';
import { isometricCameraRecipe } from './recipes';
import { ISO_PITCH } from './logic';
import {
  IsometricCameraInputSystem,
  IsometricCameraSystem,
  isometricCameraParser,
} from './systems';

/**
 * Opt-in — this plugin is deliberately NOT in `DefaultPlugins`: a scene should
 * only ever carry one camera rig, and adding this one by default would put it
 * next to the third-person and orbit cameras every existing game already uses.
 *
 * ```ts
 * withPlugin(IsometricCameraPlugin);
 * ```
 * ```html
 * <IsometricCamera ortho-size="22" distance="70"></IsometricCamera>
 * ```
 */
export const IsometricCameraPlugin: Plugin = {
  systems: [IsometricCameraInputSystem, IsometricCameraSystem],
  recipes: [isometricCameraRecipe],
  components: { IsometricCamera },
  config: {
    parsers: {
      IsometricCamera: isometricCameraParser,
    },
    defaults: {
      'isometric-camera': {
        target: 0,
        inputSource: 0,
        yawIndex: 0,
        // 45° start: the classic "corner on" isometric framing.
        yaw: Math.PI / 4,
        targetYaw: Math.PI / 4,
        rotateStep: Math.PI / 2,
        allowRotate: 1,
        qHeld: 0,
        eHeld: 0,
        pitch: ISO_PITCH,
        distance: 70,
        orthoSize: 22,
        targetOrthoSize: 22,
        minOrthoSize: 10,
        maxOrthoSize: 46,
        zoomSensitivity: 1.0,
        followLag: 0.14,
        followLagY: 0.32,
        turnLag: 0.22,
        offsetX: 0,
        offsetY: 0.9,
        offsetZ: 0,
        followX: 0,
        followY: 0,
        followZ: 0,
        initialized: 0,
      },
    },
  },
};
