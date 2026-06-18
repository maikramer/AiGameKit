// Stone adapter → engine RpgVault on the hero entity (read by the HUD
// ResourceChip resource="stone"). Thin wrapper so callers keep the same API.
import { addResource, spendResource, getResource } from 'vibegame';
import { engineState, heroEid } from '../game/engine-bridge';

const STONE = 'stone';

export function addStone(amount: number, _x = 0, _y = 0, _z = 0): void {
  const s = engineState();
  const h = heroEid();
  if (s && h) addResource(s, h, STONE, amount);
}

export function getStoneCount(): number {
  const s = engineState();
  const h = heroEid();
  return s && h ? getResource(s, h, STONE) : 0;
}

export function removeStone(amount: number): boolean {
  const s = engineState();
  const h = heroEid();
  return s && h ? spendResource(s, h, STONE, amount) : false;
}

export function removeStones(amount: number): boolean {
  return removeStone(amount);
}
