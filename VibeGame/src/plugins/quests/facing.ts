import { degToRad } from '../../shared';
import { defineQuery, defineSystem } from '../../core';
import type { State, System } from '../../core';
import { PlayerController } from '../player';
import { Transform } from '../transforms';
import {
  setTransformYawRadians,
  shortestAngleDelta,
  stepTowardYaw,
} from '../transforms/utils';
import { QuestGiver } from './components';

export { shortestAngleDelta, stepTowardYaw } from '../transforms/utils';

/**
 * Quest givers turn to face the player who walks up to them, and drift back to
 * their posted heading afterwards.
 *
 * This lives in the engine rather than in a per-NPC game script on purpose: the
 * `<GLTFLoader>` auto-idle path in `gltf-xml` deliberately skips entities that
 * own a `MonoBehaviour`, so bolting a facing script onto an NPC would silently
 * cost it its idle animation — trading one kind of statue for another.
 */

const DEFAULT_FACE_RANGE = 7;
/** rad/s — fast enough to feel attentive, slow enough not to snap. */
const TURN_SPEED = 4.5;

const giverQuery = defineQuery([QuestGiver, Transform]);
const playerQuery = defineQuery([PlayerController, Transform]);

/** Posted heading per NPC, captured the first time it is seen. */
const stateToRestYaw = new WeakMap<State, Map<number, number>>();

function restYawMap(state: State): Map<number, number> {
  let m = stateToRestYaw.get(state);
  if (!m) {
    m = new Map();
    stateToRestYaw.set(state, m);
  }
  return m;
}

export const QuestGiverFacingSystem: System = defineSystem({
  name: 'QuestGiverFacingSystem',
  group: 'simulation',
  update(state: State): void {
    const players = playerQuery(state.world);
    const player = players[0];
    if (player === undefined) return;

    const px = Transform.posX[player];
    const pz = Transform.posZ[player];
    const rangeSq = DEFAULT_FACE_RANGE * DEFAULT_FACE_RANGE;
    const maxStep = TURN_SPEED * state.time.deltaTime;
    const restYaws = restYawMap(state);

    for (const eid of giverQuery(state.world)) {
      let rest = restYaws.get(eid);
      if (rest === undefined) {
        rest = degToRad(Transform.eulerY[eid]);
        restYaws.set(eid, rest);
      }

      const dx = px - Transform.posX[eid];
      const dz = pz - Transform.posZ[eid];
      const target = dx * dx + dz * dz <= rangeSq ? Math.atan2(dx, dz) : rest;

      const current = degToRad(Transform.eulerY[eid]);
      const next = stepTowardYaw(current, target, maxStep);
      if (Math.abs(shortestAngleDelta(current, next)) < 1e-4) continue;
      setTransformYawRadians(Transform, eid, next);
    }
  },

  dispose(state: State): void {
    stateToRestYaw.delete(state);
  },
});
