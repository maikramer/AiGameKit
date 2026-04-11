import { beforeEach, describe, expect, it } from 'bun:test';
import { State } from 'vibegame';
import { AudioPlugin } from '../../../src/plugins/audio/plugin';
import {
  AudioSource,
  AudioListener,
} from '../../../src/plugins/audio/components';
import { DefaultPlugins } from '../../../src/plugins/defaults';

describe('AudioPlugin Registration', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
    state.registerPlugin(AudioPlugin);
  });

  it('should register AudioListenerSetupSystem and AudioSystem', () => {
    expect(AudioPlugin.systems).toHaveLength(2);
  });

  it('should register AudioEmitter component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, AudioSource);
    expect(state.hasComponent(entity, AudioSource)).toBe(true);
  });

  it('should register AudioListener component', () => {
    const entity = state.createEntity();
    state.addComponent(entity, AudioListener);
    expect(state.hasComponent(entity, AudioListener)).toBe(true);
  });

  it('should register both components in the plugin definition', () => {
    expect(AudioPlugin.components).toBeDefined();
    expect(AudioPlugin.components!['audioEmitter']).toBe(AudioSource);
    expect(AudioPlugin.components!['AudioListener']).toBe(AudioListener);
  });

  it('should have config.defaults for audioEmitter', () => {
    const defaults = AudioPlugin.config!.defaults!.audioEmitter;
    expect(defaults).toBeDefined();
    expect(defaults.volume).toBe(1);
    expect(defaults.loop).toBe(0);
    expect(defaults.pitch).toBe(1);
    expect(defaults.spatial).toBe(1);
    expect(defaults.minDistance).toBe(1);
    expect(defaults.maxDistance).toBe(100);
    expect(defaults.rolloff).toBe(1);
    expect(defaults.playing).toBe(0);
  });

  it('should be included in DefaultPlugins', () => {
    expect(DefaultPlugins).toContain(AudioPlugin);
  });
});
