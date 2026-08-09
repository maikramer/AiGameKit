import { addResource, getResource, spendResource } from 'vibegame';
import type { State } from 'vibegame';

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
export function createResourceAdapter(kind: string, access: ResourceAccess) {
  return {
    /** x/y/z accepted for call-site compatibility (loot drops pass a position). */
    add(amount: number, _x = 0, _y = 0, _z = 0): void {
      const s = access.state();
      const h = access.player();
      if (s && h) addResource(s, h, kind, amount);
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
