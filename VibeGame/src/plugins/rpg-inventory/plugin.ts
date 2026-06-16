import type { Plugin, Recipe } from '../../core';
import { InventoryComponent } from './components';
import { InventoryEventBridgeSystem } from './systems';

const inventoryRecipe: Recipe = {
  name: 'Inventory',
  components: ['inventory'],
};

export const InventoryPlugin: Plugin = {
  systems: [InventoryEventBridgeSystem],
  recipes: [inventoryRecipe],
  components: { inventory: InventoryComponent },
  config: {
    defaults: {
      inventory: { capacity: 20, slots: 0, version: 0 },
    },
  },
};
