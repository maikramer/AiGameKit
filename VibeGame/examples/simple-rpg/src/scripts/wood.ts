// Wood adapter → engine RpgVault on the player entity (read by the HUD
// ResourceChip resource="wood"). Thin wrapper so callers keep the same API.
import { createResourceAdapter } from '../../../shared/src/resources';
import { engineState, playerEid } from '../game/engine-bridge';

const wood = createResourceAdapter('wood', {
  state: engineState,
  player: playerEid,
});

export const addWood = wood.add;
export const getWoodCount = wood.get;
export const removeWood = wood.remove;
