import { logger } from '../../core/utils/logger';
import type { State, System } from '../../core';
import { AudioSource } from './components';
import { playAudioEmitter } from './systems';

const namedSfxRegistry = new WeakMap<State, Map<string, number>>();

function getOrCreateRegistry(state: State): Map<string, number> {
  let map = namedSfxRegistry.get(state);
  if (!map) {
    map = new Map();
    namedSfxRegistry.set(state, map);
  }
  return map;
}

export function registerNamedSfx(
  state: State,
  name: string,
  eid: number
): void {
  getOrCreateRegistry(state).set(name, eid);
}

export function playNamedSfx(state: State, name: string): void {
  if (state.headless) return;
  const eid = getOrCreateRegistry(state).get(name);
  if (eid === undefined || !state.exists(eid)) {
    logger.warn(`[audio] playNamedSfx: unknown SFX name "${name}"`);
    return;
  }
  playAudioEmitter(state, eid);
}

export const NamedSfxResolverSystem: System = {
  group: 'setup',
  update(state: State) {
    const registry = getOrCreateRegistry(state);
    for (const [name, eid] of state.getNamedEntities()) {
      if (state.hasComponent(eid, AudioSource)) {
        registry.set(name, eid);
      }
    }
  },
};
