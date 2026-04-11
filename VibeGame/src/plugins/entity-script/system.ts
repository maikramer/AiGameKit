import type { Component } from 'bitecs';

import { Parent, defineQuery, type State, type System } from '../../core';
import { GltfPending } from '../gltf-xml/components';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import { EntityScript } from './components';
import {
  deletePrevEnabled,
  deleteScriptFile,
  getCachedEntityScriptModule,
  getEntityScriptsGlob,
  getOrLoadEntityScriptModule,
  getPrevEnabled,
  getScriptFile,
  isEntityScriptSetupInflight,
  resolveEntityScriptGlobKey,
  setEntityScriptSetupInflight,
  setPrevEnabled,
} from './context';
import type { EntityScriptContext } from './types';

const entityScriptQuery = defineQuery([EntityScript]);

const parentQuery = defineQuery([Parent]);

function resolveComponent(state: State, eid: number, name: string): Component | null {
  const component = state.getComponent(name);
  if (!component) return null;
  return state.hasComponent(eid, component) ? component : null;
}

function findComponentInChildren(state: State, eid: number, name: string): Component | null {
  const onSelf = resolveComponent(state, eid, name);
  if (onSelf) return onSelf;

  for (const candidate of parentQuery(state.world)) {
    if (Parent.entity[candidate] !== eid) continue;
    const found = findComponentInChildren(state, candidate, name);
    if (found) return found;
  }
  return null;
}

function findComponentInParent(state: State, eid: number, name: string): Component | null {
  const onSelf = resolveComponent(state, eid, name);
  if (onSelf) return onSelf;

  if (!state.hasComponent(eid, Parent)) return null;
  const parentEid = Parent.entity[eid];
  if (parentEid === 0) return null;
  return findComponentInParent(state, parentEid, name);
}

export function buildContext(state: State, eid: number): EntityScriptContext {
  const root = getGltfRootGroup(state, eid);
  return {
    state,
    entity: eid,
    object3d: root ?? null,
    deltaTime: state.time.deltaTime,
    getComponent(name: string): Component | null {
      return resolveComponent(state, eid, name);
    },
    getComponentInChildren(name: string): Component | null {
      return findComponentInChildren(state, eid, name);
    },
    getComponentInParent(name: string): Component | null {
      return findComponentInParent(state, eid, name);
    },
  };
}

function shouldWaitForGltf(state: State, eid: number): boolean {
  const GltfP = state.getComponent('gltf-pending');
  if (!GltfP || !state.hasComponent(eid, GltfP)) {
    return false;
  }
  return GltfPending.loaded[eid] === 0;
}

export const EntityScriptSystem: System = {
  group: 'simulation',
  update(state: State): void {
    if (state.headless) return;

    const glob = getEntityScriptsGlob(state);

    for (const eid of entityScriptQuery(state.world)) {
      const file = getScriptFile(state, eid);
      if (!file) {
        continue;
      }

      if (EntityScript.ready[eid] === 0) {
        if (EntityScript.enabled[eid] !== 1) {
          continue;
        }

        if (shouldWaitForGltf(state, eid)) {
          continue;
        }

        if (!glob) {
          console.warn(
            `[entity-script] No script glob registered; call registerEntityScripts(state, import.meta.glob(...)). Entity ${eid}`
          );
          EntityScript.ready[eid] = 1;
          continue;
        }

        const globKey = resolveEntityScriptGlobKey(glob, file);
        if (!globKey) {
          console.warn(
            `[entity-script] No script module for "${file}" in registered glob. Entity ${eid}`
          );
          EntityScript.ready[eid] = 1;
          continue;
        }

        if (isEntityScriptSetupInflight(state, eid)) {
          continue;
        }

        setEntityScriptSetupInflight(state, eid, true);
        void getOrLoadEntityScriptModule(state, glob, globKey)
          .then(async (mod) => {
            if (!state.exists(eid)) {
              setEntityScriptSetupInflight(state, eid, false);
              return;
            }
            if (!mod) {
              console.warn(
                `[entity-script] Module for "${file}" has no start/update. Entity ${eid}`
              );
              EntityScript.ready[eid] = 1;
              setEntityScriptSetupInflight(state, eid, false);
              return;
            }
            const ctx = buildContext(state, eid);
            if (mod.awake) {
              mod.awake(ctx);
            }
            const isEnabled = EntityScript.enabled[eid] === 1;
            if (isEnabled && mod.onEnable) {
              mod.onEnable(ctx);
            }
            if (mod.start) {
              await mod.start(ctx);
            }
            if (state.exists(eid)) {
              EntityScript.ready[eid] = 1;
              setPrevEnabled(state, eid, isEnabled ? 1 : 0);
            }
            state.onDestroy(eid, () => {
              const cached = getCachedEntityScriptModule(state, globKey);
              if (cached) {
                const destroyCtx = buildContext(state, eid);
                if (EntityScript.enabled[eid] === 1 && cached.onDisable) {
                  cached.onDisable(destroyCtx);
                }
                if (cached.onDestroy) {
                  cached.onDestroy(destroyCtx);
                }
              }
              deletePrevEnabled(state, eid);
              deleteScriptFile(state, eid);
            });
            setEntityScriptSetupInflight(state, eid, false);
          })
          .catch((err: unknown) => {
            console.error(`[entity-script] Failed to load "${file}":`, err);
            if (state.exists(eid)) {
              EntityScript.ready[eid] = 1;
            }
            setEntityScriptSetupInflight(state, eid, false);
          });
        continue;
      }

      if (EntityScript.ready[eid] !== 1) {
        continue;
      }

      const glob2 = getEntityScriptsGlob(state);
      if (!glob2) {
        continue;
      }

      const globKey2 = resolveEntityScriptGlobKey(glob2, file);
      if (!globKey2) {
        continue;
      }

      const mod = getCachedEntityScriptModule(state, globKey2);
      if (!mod) {
        continue;
      }

      const curEnabled = EntityScript.enabled[eid];
      const prev = getPrevEnabled(state, eid);

      if (prev !== undefined && curEnabled !== prev) {
        const ctx = buildContext(state, eid);
        if (prev === 1 && curEnabled === 0 && mod.onDisable) {
          mod.onDisable(ctx);
        } else if (prev === 0 && curEnabled === 1 && mod.onEnable) {
          mod.onEnable(ctx);
        }
        setPrevEnabled(state, eid, curEnabled);
      }

      if (curEnabled !== 1) {
        continue;
      }

      if (!mod.update) {
        continue;
      }

      mod.update(buildContext(state, eid));
    }
  },
};

function resolveModule(state: State, eid: number): { mod: EntityScriptModule } | null {
  const file = getScriptFile(state, eid);
  if (!file) return null;

  const glob = getEntityScriptsGlob(state);
  if (!glob) return null;

  const globKey = resolveEntityScriptGlobKey(glob, file);
  if (!globKey) return null;

  const mod = getCachedEntityScriptModule(state, globKey);
  if (!mod) return null;

  return { mod };
}

export const EntityScriptFixedUpdateSystem: System = {
  group: 'fixed',
  update(state: State): void {
    if (state.headless) return;

    for (const eid of entityScriptQuery(state.world)) {
      if (EntityScript.ready[eid] !== 1 || EntityScript.enabled[eid] !== 1) {
        continue;
      }

      const resolved = resolveModule(state, eid);
      if (!resolved || !resolved.mod.fixedUpdate) {
        continue;
      }

      resolved.mod.fixedUpdate(buildContext(state, eid));
    }
  },
};

export const EntityScriptLateUpdateSystem: System = {
  group: 'late',
  update(state: State): void {
    if (state.headless) return;

    for (const eid of entityScriptQuery(state.world)) {
      if (EntityScript.ready[eid] !== 1 || EntityScript.enabled[eid] !== 1) {
        continue;
      }

      const resolved = resolveModule(state, eid);
      if (!resolved || !resolved.mod.lateUpdate) {
        continue;
      }

      resolved.mod.lateUpdate(buildContext(state, eid));
    }
  },
};
