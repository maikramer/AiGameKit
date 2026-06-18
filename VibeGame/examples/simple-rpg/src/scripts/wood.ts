// Wood adapter → engine RpgVault on the hero entity (read by the HUD
// ResourceChip resource="wood"). Thin wrapper so callers keep the same API.
import { addResource, spendResource, getResource } from 'vibegame';
import { engineState, heroEid } from '../game/engine-bridge';

const WOOD = 'wood';

export function addWood(amount: number, _x = 0, _y = 0, _z = 0): void {
  const s = engineState();
  const h = heroEid();
  if (s && h) addResource(s, h, WOOD, amount);
}

export function getWoodCount(): number {
  const s = engineState();
  const h = heroEid();
  return s && h ? getResource(s, h, WOOD) : 0;
}

export function removeWood(amount: number): boolean {
  const s = engineState();
  const h = heroEid();
  return s && h ? spendResource(s, h, WOOD, amount) : false;
}
