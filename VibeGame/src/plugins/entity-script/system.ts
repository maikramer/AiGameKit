import { defineQuery, type State, type System } from '../../core';
import { GltfPending } from '../gltf-xml/components';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import { EntityScript } from './components';
import {
  deleteScriptFile,
  getCachedEntityScriptModule,
  getEntityScriptsGlob,
  getOrLoadEntityScriptModule,
  getScriptFile,
  isEntityScriptSetupInflight,
  resolveEntityScriptGlobKey,
  setEntityScriptSetupInflight,
} from './context';
import type { EntityScriptContext } from './types';

const entityScriptQuery = defineQuery([EntityScript]);

function buildContext(state: State, eid: number): EntityScriptContext {
  const root = getGltfRootGroup(state, eid);
  return {
    state,
    entity: eid,
    object3d: root ?? null,
    deltaTime: state.time.deltaTime,
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
      if (EntityScript.enabled[eid] !== 1) {
        continue;
      }

      const file = getScriptFile(state, eid);
      if (!file) {
        continue;
      }

      if (EntityScript.ready[eid] === 0) {
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
                `[entity-script] Module for "${file}" has no setup/update. Entity ${eid}`
              );
              EntityScript.ready[eid] = 1;
              setEntityScriptSetupInflight(state, eid, false);
              return;
            }
            const ctx = buildContext(state, eid);
            if (mod.setup) {
              await mod.setup(ctx);
            }
            if (state.exists(eid)) {
              EntityScript.ready[eid] = 1;
            }
            state.onDestroy(eid, () => {
              const cached = getCachedEntityScriptModule(state, globKey);
              if (cached?.onDestroy) {
                cached.onDestroy(buildContext(state, eid));
              }
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
      const file2 = getScriptFile(state, eid);
      if (!glob2 || !file2) {
        continue;
      }

      const globKey2 = resolveEntityScriptGlobKey(glob2, file2);
      if (!globKey2) {
        continue;
      }

      const mod = getCachedEntityScriptModule(state, globKey2);
      if (!mod?.update) {
        continue;
      }

      mod.update(buildContext(state, eid));
    }
  },
};
