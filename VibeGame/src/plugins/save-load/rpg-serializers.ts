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

type ApplyArg<T> = T extends (state: State, eid: number, data: infer D) => void
  ? D
  : never;

const vaultSerializer: SaveSerializer = {
  serialize: (state, eid) => getVaultEntitySnapshot(state, eid),
  deserialize: (state, eid, data) => {
    addIfRegistered(state, eid, 'vault', VaultComponent);
    applyVaultEntitySnapshot(
      state,
      eid,
      data as ApplyArg<typeof applyVaultEntitySnapshot>
    );
  },
};

const inventorySerializer: SaveSerializer = {
  serialize: (state, eid) => getInventoryEntitySnapshot(state, eid),
  deserialize: (state, eid, data) => {
    addIfRegistered(state, eid, 'inventory', InventoryComponent);
    applyInventoryEntitySnapshot(
      state,
      eid,
      data as ApplyArg<typeof applyInventoryEntitySnapshot>
    );
  },
};

const progressionSerializer: SaveSerializer = {
  serialize: (state, eid) => getProgressionEntitySnapshot(state, eid),
  deserialize: (state, eid, data) => {
    addIfRegistered(state, eid, 'progression', ProgressionComponent);
    applyProgressionEntitySnapshot(
      state,
      eid,
      data as ApplyArg<typeof applyProgressionEntitySnapshot>
    );
  },
};

const statusSerializer: SaveSerializer = {
  serialize: (state, eid) => getStatusEffectEntitySnapshot(state, eid),
  deserialize: (state, eid, data) => {
    addIfRegistered(state, eid, 'status-effect', StatusEffectComponent);
    applyStatusEffectEntitySnapshot(
      state,
      eid,
      data as ApplyArg<typeof applyStatusEffectEntitySnapshot>
    );
  },
};

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
