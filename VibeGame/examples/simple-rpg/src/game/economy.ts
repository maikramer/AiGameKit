// Gold adapter → engine RpgVault on the player entity. Thin wrapper so the
// gameplay scripts keep calling addGold/spendGold/getGold while the actual
// balance lives in the engine vault (read by the HUD ResourceChip).
import { createResourceAdapter } from '../../../shared/src/resources';
import { engineState, playerEid } from './engine-bridge';

const gold = createResourceAdapter('gold', { state: engineState, player: playerEid });

export const addGold = gold.add;
export const spendGold = gold.remove;
export const getGold = gold.get;
