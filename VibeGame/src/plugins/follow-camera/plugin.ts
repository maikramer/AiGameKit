import type { Plugin } from '../../core';
import { FollowCamera } from './components';
import { followCameraRecipe } from './recipes';
import {
  FollowCameraAutoRotateSystem,
  FollowCameraInputSystem,
  FollowCameraPositionSystem,
  FollowCameraSetupSystem,
} from './systems';

export const FollowCameraPlugin: Plugin = {
  systems: [
    FollowCameraSetupSystem,
    FollowCameraInputSystem,
    FollowCameraAutoRotateSystem,
    FollowCameraPositionSystem,
  ],
  recipes: [followCameraRecipe],
  components: {
    FollowCamera,
  },
  config: {
    defaults: {
      'follow-camera': {
        target: 0,
        inputSource: 0,
        currentDistance: 6,
        targetDistance: 6,
        currentYaw: 0,
        targetYaw: 0,
        currentPitch: 0.35,
        targetPitch: 0.35,
        minDistance: 2,
        maxDistance: 25,
        minPitch: 0.05,
        maxPitch: Math.PI / 2.2,
        smoothness: 0.25,
        yawSmoothness: 0.07,
        positionLag: 0.14,
        offsetX: 0,
        offsetY: 1.6,
        offsetZ: 0,
        zoomSensitivity: 1.5,
        autoRotate: 1,
        autoRotateDelay: 0.4,
        lastManualInputTime: 0,
        allowManualOrbit: 1,
        sensitivity: 0.007,
        smoothedTargetX: 0,
        smoothedTargetY: 0,
        smoothedTargetZ: 0,
        smoothedTargetInit: 0,
        zoomLevel: 1,
        zoomKeyHeld: 0,
      },
    },
  },
};
