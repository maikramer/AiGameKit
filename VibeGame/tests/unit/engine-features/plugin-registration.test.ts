import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'aigamekit-vibegame';
import { YukaAiPlugin } from '../../../src/plugins/ai-yuka/plugin';
import { CombatPlugin } from '../../../src/plugins/combat/plugin';
import { CityLayoutPlugin } from '../../../src/plugins/city-layout/plugin';
import { DefaultPlugins } from '../../../src/plugins/defaults';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import { I18nPlugin } from '../../../src/plugins/i18n/plugin';
import { QuestsPlugin } from '../../../src/plugins/quests/plugin';
import { RaycastPlugin } from '../../../src/plugins/raycast/plugin';
import { RpgCorePlugin } from '../../../src/plugins/rpg-core/plugin';
import { RpgPlugins } from '../../../src/plugins/rpg-bundle';
import { SaveLoadPlugin } from '../../../src/plugins/save-load/plugin';

describe('Engine feature plugins registration', () => {
  beforeEach(() => {
    // no shared state
  });

  it('includes gameplay plugins in DefaultPlugins', () => {
    expect(DefaultPlugins).toContain(RaycastPlugin);
    expect(DefaultPlugins).toContain(YukaAiPlugin);
    expect(DefaultPlugins).toContain(HudPlugin);
    expect(DefaultPlugins).toContain(CityLayoutPlugin);
  });

  it('optional plugins are not in DefaultPlugins', () => {
    expect(DefaultPlugins).not.toContain(SaveLoadPlugin);
    expect(DefaultPlugins).not.toContain(I18nPlugin);
  });

  it('RPG plugins are not in DefaultPlugins', () => {
    expect(DefaultPlugins).not.toContain(QuestsPlugin);
    expect(DefaultPlugins).not.toContain(RpgCorePlugin);
    expect(DefaultPlugins).not.toContain(CombatPlugin);
  });

  it('RpgPlugins bundle contains QuestsPlugin', () => {
    expect(RpgPlugins).toContain(QuestsPlugin);
    expect(RpgPlugins).toContain(RpgCorePlugin);
    expect(RpgPlugins).toContain(CombatPlugin);
  });

  it('registers raycast components', () => {
    const state = new State();
    state.registerPlugin(RaycastPlugin);
    expect(state.getComponent('RaycastSource')).toBeDefined();
    expect(state.getComponent('raycastHit')).toBeDefined();
  });
});
