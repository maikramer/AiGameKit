// Stone adapter → engine RpgVault on the player entity (read by the HUD
// ResourceChip resource="stone"). Thin wrapper so callers keep the same API.
import { createResourceAdapter } from '../../../shared/src/resources';
import { engineState, playerEid } from '../game/engine-bridge';

const stone = createResourceAdapter('stone', {
  state: engineState,
  player: playerEid,
});

export const addStone = stone.add;
export const getStoneCount = stone.get;
export const removeStone = stone.remove;
export const removeStones = stone.remove;
