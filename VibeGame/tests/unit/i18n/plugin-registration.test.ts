import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { I18nPlugin } from '../../../src/plugins/i18n/plugin';
import { I18nText } from '../../../src/plugins/i18n/components';

describe('I18nPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(I18nPlugin);
  });

  it('should have recipes named "I18nText" and "I18n"', () => {
    expect(I18nPlugin.recipes!).toHaveLength(2);
    expect(I18nPlugin.recipes![0].name).toBe('I18nText');
    expect(I18nPlugin.recipes![0].components).toEqual(['i18n-text']);
    expect(I18nPlugin.recipes![1].name).toBe('I18n');
    expect(I18nPlugin.recipes![1].components).toEqual(['i18n-config']);
  });

  it('should register the i18n-text component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, I18nText);
    expect(state.hasComponent(entity, I18nText)).toBe(true);
  });

  it('should register the i18n-text recipe', () => {
    const recipe = state.getRecipe('I18nText');
    expect(recipe).toBeDefined();
    expect(recipe?.components).toContain('i18n-text');
  });

  it('should have two systems registered (I18nAutoDefaultsSystem, I18nResolveSystem)', () => {
    expect(I18nPlugin.systems).toHaveLength(2);
  });

  it('should have config.defaults for i18n-text', () => {
    const defaults = I18nPlugin.config!.defaults!['i18n-text'];
    expect(defaults).toBeDefined();
    expect(defaults.keyIndex).toBe(0);
    expect(defaults.resolved).toBe(0);
  });
});
