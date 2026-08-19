import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { Transform } from '../transforms/components';
import { PlayerController } from '../player/components';
import { Rigidbody } from '../physics/components';
import { getBodyForEntity } from '../physics/systems';
import {
  getBodyYForFeetAt,
  GROUND_CONTACT_SKIN,
} from '../physics/character-ground';
import { getGroundHeight } from '../terrain/height-sampler';
import { getBvhSurfaceHeight } from '../bvh/utils';
import {
  spawnFloatingText,
  spawnFloatingTextScreen,
} from '../floating-text/utils';
import { WorldBorder } from './components';

const borderQuery = defineQuery([WorldBorder]);
const playerQuery = defineQuery([PlayerController, Transform]);

const _tp = new THREE.Vector3();

/** Seat the teleport target on the first surface below (props/bridges win
 * over the raw terrain sample), same probe shape the spawn gate uses. */
function surfaceHeightAt(state: State, x: number, yAbove: number, z: number) {
  const bvh = getBvhSurfaceHeight(state, x, yAbove, z, 2000);
  if (bvh !== null) return bvh;
  return getGroundHeight(state, x, z);
}

/**
 * Soft circular world border. Crossing `radius` starts a `warnSeconds`
 * countdown (big screen-space numbers plus a world-space warning over the
 * player); when it expires the player is teleported to the nearest point
 * `margin` meters inside the border, seated on the surface with velocity
 * zeroed. Stepping back inside during the countdown cancels it silently.
 */
export const WorldBorderSystem: System = defineSystem({
  name: 'WorldBorderSystem',
  group: 'simulation',
  update(state: State) {
    if (state.headless) return;
    const borderEid = borderQuery(state.world)[0];
    if (borderEid === undefined) return;
    const radius = WorldBorder.radius[borderEid];
    const warnSeconds = WorldBorder.warnSeconds[borderEid];
    const margin = WorldBorder.margin[borderEid];
    const now = state.time.elapsed;
    let warnUntil = WorldBorder.warnUntil[borderEid];

    for (const player of playerQuery(state.world)) {
      const px = Transform.posX[player];
      const py = Transform.posY[player];
      const pz = Transform.posZ[player];
      const dist = Math.hypot(px, pz);
      const outside = dist > radius;

      // Countdown ticks once per whole second remaining.
      if (warnUntil > 0) {
        const remaining = warnUntil - now;
        if (outside && remaining > 0) {
          const sec = Math.ceil(remaining);
          if (sec !== WorldBorder.lastShownSecond[borderEid]) {
            WorldBorder.lastShownSecond[borderEid] = sec;
            spawnFloatingTextScreen(state, String(sec), {
              x: window.innerWidth / 2,
              y: window.innerHeight * 0.24,
              fontSizePx: 44,
              color: '#ffb060',
              duration: 1.0,
            });
          }
        }
        if (!outside) {
          // Came back inside in time — cancel silently.
          warnUntil = 0;
          WorldBorder.lastShownSecond[borderEid] = 0;
        } else if (remaining <= 0) {
          // Expired: teleport to the nearest point inside the border.
          const target = Math.max(radius - margin, 1);
          const k = target / Math.max(dist, 1e-6);
          const nx = px * k;
          const nz = pz * k;
          const groundY = surfaceHeightAt(state, nx, py + 8, nz);
          const bodyY = getBodyYForFeetAt(
            state,
            player,
            groundY + GROUND_CONTACT_SKIN
          );

          const body = getBodyForEntity(state, player);
          Transform.posX[player] = nx;
          Transform.posY[player] = bodyY;
          Transform.posZ[player] = nz;
          if (body) {
            Rigidbody.posX[player] = nx;
            Rigidbody.posY[player] = bodyY;
            Rigidbody.posZ[player] = nz;
            Rigidbody.velX[player] = 0;
            Rigidbody.velY[player] = 0;
            Rigidbody.velZ[player] = 0;
            _tp.set(nx, bodyY, nz);
            body.setTranslation(_tp, true);
            body.setLinvel({ x: 0, y: 0, z: 0 }, true);
            body.setAngvel({ x: 0, y: 0, z: 0 }, true);
            body.wakeUp();
          }

          warnUntil = 0;
          WorldBorder.lastShownSecond[borderEid] = 0;
          WorldBorder.teleported[borderEid] = 1;
          spawnFloatingText(state, 'De volta à fronteira', {
            x: nx,
            y: bodyY + 2.2,
            z: nz,
            color: '#ffd9a0',
            size: 0.5,
            duration: 1.8,
          });
          continue;
        }
      } else if (outside) {
        warnUntil = now + warnSeconds;
        WorldBorder.lastShownSecond[borderEid] = 0;
        spawnFloatingText(state, 'Fronteira do vale — volte!', {
          x: px,
          y: py + 2.4,
          z: pz,
          color: '#ffb060',
          size: 0.45,
          duration: 1.8,
        });
      }
    }

    WorldBorder.warnUntil[borderEid] = warnUntil;
  },
});
