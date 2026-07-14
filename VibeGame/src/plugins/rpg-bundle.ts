import type { Plugin } from '../core';
import { CombatPlugin } from './combat/plugin';
import { HudRpgPlugin } from './hud/rpg-plugin';
import { EconomyPlugin } from './rpg-economy/plugin';
import { RpgAiPlugin } from './rpg-ai/plugin';
import { RpgCorePlugin } from './rpg-core/plugin';
import { InventoryPlugin } from './rpg-inventory/plugin';
import { PauseCoordinatorPlugin } from './rpg-pause/plugin';
import { ProgressionPlugin } from './rpg-progression/plugin';
import { ResourceNodePlugin } from './rpg-resource-node/plugin';
import { StatusEffectsPlugin } from './rpg-status/plugin';
import { RpgVaultPlugin } from './rpg-vault/plugin';
import { QuestsPlugin } from './quests/plugin';

/** Opt-in RPG plugin bundle for games like simple-rpg. Not part of DefaultPlugins. */
export const RpgPlugins: Plugin[] = [
  RpgCorePlugin,
  QuestsPlugin,
  RpgVaultPlugin,
  InventoryPlugin,
  ProgressionPlugin,
  PauseCoordinatorPlugin,
  ResourceNodePlugin,
  EconomyPlugin,
  CombatPlugin,
  StatusEffectsPlugin,
  RpgAiPlugin,
  HudRpgPlugin,
];
