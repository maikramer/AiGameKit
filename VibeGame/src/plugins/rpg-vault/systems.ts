import type { State, System } from '../../core';
import { pruneVaults } from './components';

// addResource/spendResource emit ECONOMY_GAINED/ECONOMY_SPENT synchronously
// (callers assert handlers fired immediately after the call). This system owns
// the per-frame lifecycle: dropping side-table entries once their entity is gone,
// so destroyed vaults do not leak.
export const VaultEventBridgeSystem: System = {
  group: 'simulation',
  update(state: State): void {
    pruneVaults(state, (eid) => state.exists(eid));
  },
};
