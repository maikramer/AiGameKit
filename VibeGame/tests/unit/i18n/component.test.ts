import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { I18nText } from '../../../src/plugins/i18n/components';

const I18N_FIELDS = ['keyIndex', 'resolved'] as const;

describe('I18nText Component', () => {
  let state: State;
  let entity: number;

  beforeEach(() => {
    state = new State();
    entity = state.createEntity();
  });

  it('should have all fields defined', () => {
    for (const field of I18N_FIELDS) {
      expect(I18nText[field]).toBeDefined();
      expect(typeof I18nText[field][entity]).toBe('number');
    }
  });

  it('should initialize all fields to 0', () => {
    state.addComponent(entity, I18nText);

    for (const field of I18N_FIELDS) {
      expect(I18nText[field][entity]).toBe(0);
    }
  });

  it('should allow writing and reading keyIndex', () => {
    state.addComponent(entity, I18nText);
    I18nText.keyIndex[entity] = 42;
    expect(I18nText.keyIndex[entity]).toBe(42);
  });

  it('should allow writing and reading resolved', () => {
    state.addComponent(entity, I18nText);
    I18nText.resolved[entity] = 1;
    expect(I18nText.resolved[entity]).toBe(1);
  });

  it('should support multiple entities with independent values', () => {
    state.addComponent(entity, I18nText);
    const entity2 = state.createEntity();
    state.addComponent(entity2, I18nText);

    I18nText.keyIndex[entity] = 10;
    I18nText.keyIndex[entity2] = 20;
    I18nText.resolved[entity] = 0;
    I18nText.resolved[entity2] = 1;

    expect(I18nText.keyIndex[entity]).toBe(10);
    expect(I18nText.keyIndex[entity2]).toBe(20);
    expect(I18nText.resolved[entity]).toBe(0);
    expect(I18nText.resolved[entity2]).toBe(1);
  });
});
