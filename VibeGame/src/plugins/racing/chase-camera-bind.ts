import { defineSystem, defineQuery, type System } from '../../core';
import { ChaseCamera, PlayerVehicle } from './components';

const chaseCamBindQuery = defineQuery([ChaseCamera]);
const playerVehicleQuery = defineQuery([PlayerVehicle]);

// Pending name→entity bindings written by the ChaseCamera parser (parse order
// isn't guaranteed, so we resolve lazily in a `simulation` system).
export const pendingCameraTargets = new Map<number, string>();

/**
 * Resolve `<ChaseCamera target="name">` once the named vehicle exists, and
 * auto-bind any unbound chase camera to the first PlayerVehicle. Mirrors the
 * engine's `PlayerCameraLinkingSystem` auto-bind behavior.
 */
export const ChaseCameraBindSystem: System = defineSystem({
  name: 'ChaseCameraBindSystem',
  group: 'simulation',
  update(state) {
    const cams = chaseCamBindQuery(state.world);
    if (cams.length === 0) return;

    // First pass: resolve named targets.
    if (pendingCameraTargets.size > 0) {
      for (const cam of cams) {
        const name = pendingCameraTargets.get(cam);
        if (!name) continue;
        const target = state.getEntityByName(name);
        if (target != null) {
          ChaseCamera.target[cam] = target;
          pendingCameraTargets.delete(cam);
        }
      }
    }

    // Second pass: auto-bind any still-unbound camera to the first player vehicle.
    const players = playerVehicleQuery(state.world);
    if (players.length > 0) {
      const player = players[0]!;
      for (const cam of cams) {
        if (ChaseCamera.target[cam] === 0) {
          ChaseCamera.target[cam] = player;
        }
      }
    }
  },
});
