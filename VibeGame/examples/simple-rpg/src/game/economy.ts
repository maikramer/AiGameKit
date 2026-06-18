// Gold adapter → engine RpgVault on the hero entity. Kept as a thin module so
// the gameplay scripts keep calling addGold/spendGold/getGold while the actual
// balance lives in the engine vault (read by the HUD ResourceChip).
import { addResource, spendResource, getResource } from 'vibegame';
import { engineState, heroEid } from './engine-bridge';

const GOLD = 'gold';

// x/y/z accepted for call-site compatibility (loot drops pass a position); the
// engine vault is positionless, so they are ignored.
export function addGold(amount: number, _x = 0, _y = 0, _z = 0): void {
  const s = engineState();
  const h = heroEid();
  if (s && h) addResource(s, h, GOLD, amount);
}

export function spendGold(amount: number): boolean {
  const s = engineState();
  const h = heroEid();
  return s && h ? spendResource(s, h, GOLD, amount) : false;
}

export function getGold(): number {
  const s = engineState();
  const h = heroEid();
  return s && h ? getResource(s, h, GOLD) : 0;
}
