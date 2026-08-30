import { addResource, getResource, spendResource } from 'aigamekit-vibegame';
import type { State } from 'aigamekit-vibegame';

export interface ResourceAccess {
  state(): State | null;
  player(): number;
}

/**
 * Thin adapter over the engine RpgVault resource API, bound to a player entity.
 * Keeps call sites on the same add/get/remove API while the balance lives in
 * the engine vault (read by the HUD ResourceChip).
 *
 * Replaces the copy-paste gold/wood/stone adapter modules — each was the same
 * file with a different resource id.
 */
/**
 * Coerce a resource amount to a finite non-negative number. NaN/Infinity
 * (e.g. a debug console call with a typo'd expression) would poison the vault
 * balance permanently — `getResource` would keep returning NaN forever.
 */
function saneAmount(amount: number): number {
  return Number.isFinite(amount) && amount > 0 ? amount : 0;
}

export function createResourceAdapter(kind: string, access: ResourceAccess) {
  return {
    /** x/y/z accepted for call-site compatibility (loot drops pass a position). */
    add(amount: number, _x = 0, _y = 0, _z = 0): void {
      const n = saneAmount(amount);
      if (n <= 0) return;
      const s = access.state();
      const h = access.player();
      if (s && h) addResource(s, h, kind, n);
    },
    remove(amount: number): boolean {
      const s = access.state();
      const h = access.player();
      return s && h ? spendResource(s, h, kind, amount) : false;
    },
    get(): number {
      const s = access.state();
      const h = access.player();
      return s && h ? getResource(s, h, kind) : 0;
    },
  };
}
