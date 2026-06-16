export {
  DEFAULT_VAULT_CAPACITY,
  VaultComponent,
  addResource,
  applyVaultEntitySnapshot,
  getCapacity,
  getResource,
  getVaultEntitySnapshot,
  registerResourceKind,
  setCapacity,
  spendResource,
} from './components';
export type { VaultEntitySnapshot, VaultResourceSlotData } from './components';
export { getResourceKindIndex, pruneVaults } from './components';
export { VaultEventBridgeSystem } from './systems';
export { RpgVaultPlugin } from './plugin';
