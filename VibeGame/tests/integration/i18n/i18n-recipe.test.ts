import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { HudPlugin } from '../../../src/plugins/hud/plugin';
import { I18nPlugin } from '../../../src/plugins/i18n/plugin';
import { I18nText } from '../../../src/plugins/i18n/components';
import { HudPanel } from '../../../src/plugins/hud/components';

describe('I18n Integration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.headless = true;
  });

  it('should register without error when HudPlugin is registered first', () => {
    state.registerPlugin(HudPlugin);
    state.registerPlugin(I18nPlugin);

    const entity = state.createEntity();
    state.addComponent(entity, I18nText);
    expect(state.hasComponent(entity, I18nText)).toBe(true);
  });

  it('should allow creating both hud-panel and i18n-text entities', () => {
    state.registerPlugin(HudPlugin);
    state.registerPlugin(I18nPlugin);

    const hud = state.createEntity();
    state.addComponent(hud, HudPanel);
    HudPanel.textIndex[hud] = 0;

    const i18n = state.createEntity();
    state.addComponent(i18n, I18nText);
    I18nText.keyIndex[i18n] = 5;
    I18nText.resolved[i18n] = 0;

    expect(state.hasComponent(hud, HudPanel)).toBe(true);
    expect(state.hasComponent(i18n, I18nText)).toBe(true);
    expect(I18nText.keyIndex[i18n]).toBe(5);
    expect(HudPanel.textIndex[hud]).toBe(0);
  });

  it('should register i18n-text recipe and resolve it via getRecipe', () => {
    state.registerPlugin(HudPlugin);
    state.registerPlugin(I18nPlugin);

    const recipe = state.getRecipe('i18n-text');
    expect(recipe).toBeDefined();
    expect(recipe?.name).toBe('i18n-text');
  });
});
