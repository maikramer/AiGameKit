import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { SaveLoadPlugin } from '../../../src/plugins/save-load/plugin';
import { Serializable } from '../../../src/plugins/save-load/components';

describe('SaveLoadPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(SaveLoadPlugin);
  });

  it('should have no recipes (optional plugin)', () => {
    expect(SaveLoadPlugin.recipes).toBeUndefined();
  });

  it('should register the serializable component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, Serializable);
    expect(state.hasComponent(entity, Serializable)).toBe(true);
  });

  it('should have one system registered (SerializationIdSystem)', () => {
    expect(SaveLoadPlugin.systems).toHaveLength(1);
  });

  it('should have config.defaults for serializable', () => {
    const defaults = SaveLoadPlugin.config!.defaults!.serializable;
    expect(defaults).toBeDefined();
    expect(defaults.flag).toBe(0);
    expect(defaults.serializationId).toBe(0);
  });

  it('should not have config.enums', () => {
    expect(SaveLoadPlugin.config!.enums).toBeUndefined();
  });
});
