import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { AiSteeringPlugin } from '../../../src/plugins/ai-steering/plugin';
import { DefaultPlugins } from '../../../src/plugins/defaults';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import { I18nPlugin } from '../../../src/plugins/i18n/plugin';
import { RaycastPlugin } from '../../../src/plugins/raycast/plugin';
import { SaveLoadPlugin } from '../../../src/plugins/save-load/plugin';

describe('Engine feature plugins registration', () => {
  beforeEach(() => {
    // no shared state
  });

  it('includes gameplay plugins in DefaultPlugins', () => {
    expect(DefaultPlugins).toContain(RaycastPlugin);
    expect(DefaultPlugins).toContain(AiSteeringPlugin);
    expect(DefaultPlugins).toContain(HudPlugin);
  });

  it('optional plugins are not in DefaultPlugins', () => {
    expect(DefaultPlugins).not.toContain(SaveLoadPlugin);
    expect(DefaultPlugins).not.toContain(I18nPlugin);
  });

  it('registers raycast components', () => {
    const state = new State();
    state.registerPlugin(RaycastPlugin);
    expect(state.getComponent('RaycastSource')).toBeDefined();
    expect(state.getComponent('raycastHit')).toBeDefined();
  });
});
