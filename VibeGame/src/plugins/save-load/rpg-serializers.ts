import type { Component, State } from '../../core';
import {
  VaultComponent,
  applyVaultEntitySnapshot,
  getVaultEntitySnapshot,
} from '../rpg-vault';
import {
  InventoryComponent,
  applyInventoryEntitySnapshot,
  getInventoryEntitySnapshot,
} from '../rpg-inventory';
import {
  ProgressionComponent,
  applyProgressionEntitySnapshot,
  getProgressionEntitySnapshot,
} from '../rpg-progression';
import {
  StatusEffectComponent,
  applyStatusEffectEntitySnapshot,
  getStatusEffectEntitySnapshot,
} from '../rpg-status';
import { ParticleEmitter } from '../particles/components';
import {
  registerSaveSerializer,
  registerTransientExclusion,
  type SaveSerializer,
} from './serializer-registry';

export const VAULT_SERIALIZER_KIND = 'vault';
export const INVENTORY_SERIALIZER_KIND = 'inventory';
export const PROGRESSION_SERIALIZER_KIND = 'progression';
export const STATUS_SERIALIZER_KIND = 'status-effect';

function addIfRegistered(
  state: State,
  eid: number,
  componentName: string,
  component: Component
): void {
  if (state.getComponent(componentName)) {
    state.addComponent(eid, component);
  }
}

/**
 * Build an entity serializer from a component-name + snapshot pair. The four
 * RPG serializers below are the same shape with different components — this
 * factory is the single implementation.
 */
function makeEntitySerializer<G, A>(
  componentName: string,
  component: Component,
  getSnapshot: (state: State, eid: number) => G,
  applySnapshot: (state: State, eid: number, data: A) => void
): SaveSerializer {
  return {
    serialize: (state, eid) => getSnapshot(state, eid),
    deserialize: (state, eid, data) => {
      addIfRegistered(state, eid, componentName, component);
      applySnapshot(state, eid, data as A);
    },
  };
}

const vaultSerializer: SaveSerializer = makeEntitySerializer(
  'vault',
  VaultComponent,
  getVaultEntitySnapshot,
  applyVaultEntitySnapshot
);

const inventorySerializer: SaveSerializer = makeEntitySerializer(
  'inventory',
  InventoryComponent,
  getInventoryEntitySnapshot,
  applyInventoryEntitySnapshot
);

const progressionSerializer: SaveSerializer = makeEntitySerializer(
  'progression',
  ProgressionComponent,
  getProgressionEntitySnapshot,
  applyProgressionEntitySnapshot
);

const statusSerializer: SaveSerializer = makeEntitySerializer(
  'status-effect',
  StatusEffectComponent,
  getStatusEffectEntitySnapshot,
  applyStatusEffectEntitySnapshot
);

let transientExclusionsRegistered = false;

function registerTransientExclusions(): void {
  if (transientExclusionsRegistered) return;
  transientExclusionsRegistered = true;
  registerTransientExclusion({
    name: 'projectile',
    component: 'projectile-data',
  });
  registerTransientExclusion({
    name: 'floating-text',
    component: 'floating-text',
  });
  registerTransientExclusion({
    name: 'particle-burst',
    component: 'particle-emitter',
    matches: (_state, eid) => ParticleEmitter.burst[eid] === 1,
  });
}

export function registerRpgSaveSerializers(state: State): void {
  registerTransientExclusions();
  if (state.getComponent('vault')) {
    registerSaveSerializer(state, VAULT_SERIALIZER_KIND, vaultSerializer);
  }
  if (state.getComponent('inventory')) {
    registerSaveSerializer(
      state,
      INVENTORY_SERIALIZER_KIND,
      inventorySerializer
    );
  }
  if (state.getComponent('progression')) {
    registerSaveSerializer(
      state,
      PROGRESSION_SERIALIZER_KIND,
      progressionSerializer
    );
  }
  if (state.getComponent('status-effect')) {
    registerSaveSerializer(state, STATUS_SERIALIZER_KIND, statusSerializer);
  }
}
