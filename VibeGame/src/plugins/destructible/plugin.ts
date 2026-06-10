import * as THREE from 'three';
import type { Adapter, Plugin } from '../../core';
import { Destructible } from './components';
import { DestructibleSystem } from './systems';
import { setDestructiblePopupText } from './utils';

const _color = new THREE.Color();

export const DestructiblePlugin: Plugin = {
  systems: [DestructibleSystem],
  components: { destructible: Destructible },
  config: {
    defaults: {
      destructible: {
        hits: 1,
        hitsTaken: 0,
        range: 3.5,
        impactFraction: 0.75,
        pendingImpact: 0,
        preset: 5, // explosion
        burstCount: 60,
        faceOnHit: 1,
        sparkOnHit: 1,
        popupColorR: 1,
        popupColorG: 1,
        popupColorB: 1,
        popupSize: 0.4,
      },
    },
    enums: {
      destructible: {
        preset: {
          fire: 0,
          rain: 1,
          snow: 2,
          smoke: 3,
          dust: 4,
          explosion: 5,
          sparks: 6,
          magic: 7,
          fireflies: 8,
        },
      },
    },
    adapters: {
      destructible: {
        // Strings can't live in SOA fields — sidecar map.
        'popup-text': ((entity, value, state) => {
          setDestructiblePopupText(state, entity, String(value));
        }) as Adapter,
        'popup-color': ((entity, value) => {
          _color.set(
            String(value).startsWith('0x')
              ? parseInt(String(value), 16)
              : String(value)
          );
          Destructible.popupColorR[entity] = _color.r;
          Destructible.popupColorG[entity] = _color.g;
          Destructible.popupColorB[entity] = _color.b;
        }) as Adapter,
      },
    },
  },
};
