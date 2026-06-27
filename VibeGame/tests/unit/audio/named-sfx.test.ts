import { beforeEach, describe, expect, it, mock } from 'bun:test';
import { State } from '../../../src/core';

mock.module('howler', () => ({
  Howl: class MockHowl {
    play = mock(() => 1);
    stop = mock(() => {});
    volume = mock(() => {});
    rate = mock(() => {});
    loop = mock(() => {});
    pos = mock(() => {});
    pannerAttr = mock(() => {});
    once = mock(() => {});
    unload = mock(() => {});
  },
  Howler: { pos: () => {}, ctx: { state: 'running', resume: () => {} } },
}));

const { AudioSource } = await import('../../../src/plugins/audio/components');
const { registerNamedSfx, playNamedSfx, NamedSfxResolverSystem } = await import(
  '../../../src/plugins/audio/sfx-registry'
);

describe('NamedSfxRegistry', () => {
  let state: State;

  beforeEach(() => {
    state = new State();
  });

  describe('auto-populate from AudioSource entities', () => {
    it('registers named AudioSource entities into the registry', () => {
      const eid = state.createEntity();
      state.addComponent(eid, AudioSource);
      state.setEntityName('sfx-jump', eid);

      NamedSfxResolverSystem.update!(state);

      playNamedSfx(state, 'sfx-jump');
      expect(AudioSource.playing[eid]).toBe(1);
    });

    it('ignores AudioSource entities without a name', () => {
      const eid = state.createEntity();
      state.addComponent(eid, AudioSource);

      const warn = mock(() => {});
      const original = console.warn;
      console.warn = warn;
      try {
        playNamedSfx(state, 'sfx-jump');
      } finally {
        console.warn = original;
      }

      expect(AudioSource.playing[eid]).toBe(0);
      expect(warn).toHaveBeenCalled();
    });

    it('skips named entities that have no AudioSource', () => {
      const eid = state.createEntity();
      state.setEntityName('hero', eid);

      NamedSfxResolverSystem.update!(state);

      const warn = mock(() => {});
      const original = console.warn;
      console.warn = warn;
      try {
        playNamedSfx(state, 'hero');
      } finally {
        console.warn = original;
      }
      expect(warn).toHaveBeenCalled();
    });
  });

  describe('playNamedSfx delegation', () => {
    it('delegates to playAudioEmitter with the resolved eid', () => {
      const eid = state.createEntity();
      state.addComponent(eid, AudioSource);
      registerNamedSfx(state, 'coin', eid);

      playNamedSfx(state, 'coin');
      expect(AudioSource.playing[eid]).toBe(1);
    });

    it('re-registering the same name updates the mapped eid', () => {
      const first = state.createEntity();
      state.addComponent(first, AudioSource);
      const second = state.createEntity();
      state.addComponent(second, AudioSource);
      registerNamedSfx(state, 'coin', first);
      registerNamedSfx(state, 'coin', second);

      playNamedSfx(state, 'coin');
      expect(AudioSource.playing[first]).toBe(0);
      expect(AudioSource.playing[second]).toBe(1);
    });
  });

  describe('fallback graceful', () => {
    it('warns and does not crash when the name is unknown', () => {
      const warn = mock(() => {});
      const original = console.warn;
      console.warn = warn;
      try {
        expect(() => playNamedSfx(state, 'nonexistent')).not.toThrow();
      } finally {
        console.warn = original;
      }
      expect(warn).toHaveBeenCalledTimes(1);
    });

    it('is a no-op in headless mode', () => {
      const eid = state.createEntity();
      state.addComponent(eid, AudioSource);
      registerNamedSfx(state, 'coin', eid);
      state.headless = true;

      playNamedSfx(state, 'coin');
      expect(AudioSource.playing[eid]).toBe(0);
    });
  });
});
