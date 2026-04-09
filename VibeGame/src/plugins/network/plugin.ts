import type { Plugin } from '../../core';
import { NetworkBuffer, Networked } from './components';
import { networkedPlayerRecipe } from './recipes';
import {
  NetworkConnectSystem,
  NetworkInterpolationSystem,
  NetworkSendSystem,
} from './systems';

export const NetworkPlugin: Plugin = {
  systems: [
    NetworkConnectSystem,
    NetworkSendSystem,
    NetworkInterpolationSystem,
  ],
  recipes: [networkedPlayerRecipe],
  components: {
    networked: Networked,
    networkBuffer: NetworkBuffer,
  },
  config: {
    defaults: {
      networked: {
        networkId: 0,
        isOwner: 1,
        interpolate: 1,
      },
      networkBuffer: {
        prevX: 0,
        prevY: 0,
        prevZ: 0,
        prevRotX: 0,
        prevRotY: 0,
        prevRotZ: 0,
        prevRotW: 1,
        prevScaleX: 1,
        prevScaleY: 1,
        prevScaleZ: 1,
        nextX: 0,
        nextY: 0,
        nextZ: 0,
        nextRotX: 0,
        nextRotY: 0,
        nextRotZ: 0,
        nextRotW: 1,
        nextScaleX: 1,
        nextScaleY: 1,
        nextScaleZ: 1,
      },
    },
  },
};
