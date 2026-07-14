import { logger } from '../../core/utils/logger';
import type { Component } from '../../core';

import {
  Parent,
  Tag,
  Layer,
  defineQuery,
  type State,
  type System,
} from '../../core';
import { MAX_ENTITIES } from '../../core/ecs/constants';
import {
  startCoroutine,
  stopAllCoroutines,
  stopCoroutine,
} from '../../core/ecs/coroutines';
import { getTagName } from '../../core/ecs/tags';
import { Collider, TouchedEvent, TouchEndedEvent } from '../physics/components';
import { Transform } from '../transforms/components';
import { GltfPending } from '../gltf-xml/components';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import { MonoBehaviour } from './components';
import {
  addActiveCollisionPair,
  deletePrevEnabled,
  deleteScriptFile,
  getActiveCollisionPairs,
  getCachedMonoBehaviourModule,
  getEntityScriptsGlob,
  getOrLoadMonoBehaviourModule,
  getPrevEnabled,
  getScriptFile,
  isEntityScriptSetupInflight,
  removeActiveCollisionPair,
  resolveEntityScriptGlobKey,
  setEntityScriptSetupInflight,
  setPrevEnabled,
} from './context';
import type {
  CollisionOther,
  MonoBehaviourContext,
  MonoBehaviourModule,
} from './types';

const entityScriptQuery = defineQuery([MonoBehaviour]);
const parentQuery = defineQuery([Parent]);
const touchedWithScriptQuery = defineQuery([MonoBehaviour, TouchedEvent]);
const touchEndedWithScriptQuery = defineQuery([MonoBehaviour, TouchEndedEvent]);

/** Packed (eid, other) key — avoids `${eid}:${other}` string allocs. */
function collisionPairKey(eid: number, other: number): number {
  return eid * MAX_ENTITIES + other;
}

const _enteredPairs = new Set<number>();
const _collisionOther: CollisionOther = { entity: 0 };

/**
 * Per-entity pooled MonoBehaviourContext. Getters close over a mutable slot so
 * `buildContext` is O(1) field writes instead of allocating a fresh object tree
 * every `update`/`fixedUpdate`/`lateUpdate`/collision callback.
 */
type CtxSlot = {
  state: State;
  eid: number;
  hasTransform: boolean;
  hasTag: boolean;
  hasLayer: boolean;
  ctx: MonoBehaviourContext;
};

const ctxPoolByState = new WeakMap<State, Map<number, CtxSlot>>();

function getCtxSlot(state: State, eid: number): CtxSlot {
  let byEid = ctxPoolByState.get(state);
  if (!byEid) {
    byEid = new Map();
    ctxPoolByState.set(state, byEid);
  }
  let slot = byEid.get(eid);
  if (slot) return slot;

  slot = {
    state,
    eid,
    hasTransform: false,
    hasTag: false,
    hasLayer: false,
    ctx: null as unknown as MonoBehaviourContext,
  };
  const s = slot;
  s.ctx = {
    get state() {
      return s.state;
    },
    set state(v: State) {
      s.state = v;
    },
    get entity() {
      return s.eid;
    },
    set entity(v: number) {
      s.eid = v;
    },
    object3d: null,
    deltaTime: 0,
    gameObject: {
      get id() {
        return s.eid;
      },
      get name() {
        return `Entity_${s.eid}`;
      },
      get tag() {
        return s.hasTag ? getTagName(Tag.value[s.eid]) : 'Untagged';
      },
      get layer() {
        return s.hasLayer ? Layer.value[s.eid] : 0;
      },
    },
    transform: {
      get positionX() {
        return s.hasTransform ? Transform.posX[s.eid] : 0;
      },
      get positionY() {
        return s.hasTransform ? Transform.posY[s.eid] : 0;
      },
      get positionZ() {
        return s.hasTransform ? Transform.posZ[s.eid] : 0;
      },
      get rotationX() {
        return s.hasTransform ? Transform.eulerX[s.eid] : 0;
      },
      get rotationY() {
        return s.hasTransform ? Transform.eulerY[s.eid] : 0;
      },
      get rotationZ() {
        return s.hasTransform ? Transform.eulerZ[s.eid] : 0;
      },
      get scaleX() {
        return s.hasTransform ? Transform.scaleX[s.eid] : 1;
      },
      get scaleY() {
        return s.hasTransform ? Transform.scaleY[s.eid] : 1;
      },
      get scaleZ() {
        return s.hasTransform ? Transform.scaleZ[s.eid] : 1;
      },
    },
    getComponent(name: string): Component | null {
      return resolveComponent(s.state, s.eid, name);
    },
    getComponentInChildren(name: string): Component | null {
      return findComponentInChildren(s.state, s.eid, name);
    },
    getComponentInParent(name: string): Component | null {
      return findComponentInParent(s.state, s.eid, name);
    },
    StartCoroutine(genOrFn: Generator | (() => Generator)): number {
      return startCoroutine(s.state, s.eid, genOrFn);
    },
    StopCoroutine(coroutineId: number): void {
      stopCoroutine(s.state, s.eid, coroutineId);
    },
    StopAllCoroutines(): void {
      stopAllCoroutines(s.state, s.eid);
    },
  };
  byEid.set(eid, s);
  state.onDestroy(eid, () => {
    ctxPoolByState.get(state)?.delete(eid);
  });
  return s;
}

export function buildContext(state: State, eid: number): MonoBehaviourContext {
  const slot = getCtxSlot(state, eid);
  slot.state = state;
  slot.eid = eid;
  slot.hasTransform = state.hasComponent(eid, Transform);
  slot.hasTag = state.hasComponent(eid, Tag);
  slot.hasLayer = state.hasComponent(eid, Layer);
  const root = getGltfRootGroup(state, eid);
  slot.ctx.object3d = root ?? null;
  slot.ctx.deltaTime = state.time.deltaTime;
  return slot.ctx;
}

function resolveComponent(
  state: State,
  eid: number,
  name: string
): Component | null {
  const component = state.getComponent(name);
  if (!component) return null;
  return state.hasComponent(eid, component) ? component : null;
}

function findComponentInChildren(
  state: State,
  eid: number,
  name: string
): Component | null {
  const onSelf = resolveComponent(state, eid, name);
  if (onSelf) return onSelf;

  for (const candidate of parentQuery(state.world)) {
    if (Parent.entity[candidate] !== eid) continue;
    const found = findComponentInChildren(state, candidate, name);
    if (found) return found;
  }
  return null;
}

function findComponentInParent(
  state: State,
  eid: number,
  name: string
): Component | null {
  const onSelf = resolveComponent(state, eid, name);
  if (onSelf) return onSelf;

  if (!state.hasComponent(eid, Parent)) return null;
  const parentEid = Parent.entity[eid];
  if (parentEid === 0) return null;
  return findComponentInParent(state, parentEid, name);
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

      if (MonoBehaviour.ready[eid] === 0) {
        if (MonoBehaviour.enabled[eid] !== 1) {
          continue;
        }

        if (shouldWaitForGltf(state, eid)) {
          continue;
        }

        if (!glob) {
          logger.warn(
            `[entity-script] No script glob registered; call registerEntityScripts(state, import.meta.glob(...)). Entity ${eid}`
          );
          MonoBehaviour.ready[eid] = 1;
          continue;
        }

        const globKey = resolveEntityScriptGlobKey(glob, file);
        if (!globKey) {
          logger.warn(
            `[entity-script] No script module for "${file}" in registered glob. Entity ${eid}`
          );
          MonoBehaviour.ready[eid] = 1;
          continue;
        }

        if (isEntityScriptSetupInflight(state, eid)) {
          continue;
        }

        setEntityScriptSetupInflight(state, eid, true);
        void getOrLoadMonoBehaviourModule(state, glob, globKey)
          .then(async (mod) => {
            if (!state.exists(eid)) {
              setEntityScriptSetupInflight(state, eid, false);
              return;
            }
            if (!mod) {
              logger.warn(
                `[entity-script] Module for "${file}" has no start/update. Entity ${eid}`
              );
              MonoBehaviour.ready[eid] = 1;
              setEntityScriptSetupInflight(state, eid, false);
              return;
            }
            const ctx = buildContext(state, eid);
            if (mod.awake) {
              mod.awake(ctx);
            }
            const isEnabled = MonoBehaviour.enabled[eid] === 1;
            if (isEnabled && mod.onEnable) {
              mod.onEnable(ctx);
            }
            if (mod.start) {
              await mod.start(ctx);
            }
            if (state.exists(eid)) {
              MonoBehaviour.ready[eid] = 1;
              setPrevEnabled(state, eid, isEnabled ? 1 : 0);
            }
            state.onDestroy(eid, () => {
              const cached = getCachedMonoBehaviourModule(state, globKey);
              if (cached) {
                const destroyCtx = buildContext(state, eid);
                if (MonoBehaviour.enabled[eid] === 1 && cached.onDisable) {
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
            logger.error(`[entity-script] Failed to load "${file}":`, err);
            if (state.exists(eid)) {
              MonoBehaviour.ready[eid] = 1;
            }
            setEntityScriptSetupInflight(state, eid, false);
          });
        continue;
      }

      if (MonoBehaviour.ready[eid] !== 1) {
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

      const mod = getCachedMonoBehaviourModule(state, globKey2);
      if (!mod) {
        continue;
      }

      const curEnabled = MonoBehaviour.enabled[eid];
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

function resolveModule(
  state: State,
  eid: number
): { mod: MonoBehaviourModule } | null {
  const file = getScriptFile(state, eid);
  if (!file) return null;

  const glob = getEntityScriptsGlob(state);
  if (!glob) return null;

  const globKey = resolveEntityScriptGlobKey(glob, file);
  if (!globKey) return null;

  const mod = getCachedMonoBehaviourModule(state, globKey);
  if (!mod) return null;

  return { mod };
}

export const EntityScriptFixedUpdateSystem: System = {
  group: 'fixed',
  update(state: State): void {
    if (state.headless) return;

    for (const eid of entityScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1) {
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
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1) {
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

function isTriggerCollision(state: State, eid1: number, eid2: number): boolean {
  const hasC1 = state.hasComponent(eid1, Collider);
  const hasC2 = state.hasComponent(eid2, Collider);
  if (hasC1 && Collider.isSensor[eid1] === 1) return true;
  if (hasC2 && Collider.isSensor[eid2] === 1) return true;
  return false;
}

export const EntityScriptCollisionBridgeSystem: System = {
  group: 'simulation',
  update(state: State): void {
    if (state.headless) return;

    _enteredPairs.clear();

    for (const eid of touchedWithScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1)
        continue;

      const other = TouchedEvent.other[eid];
      const trigger = isTriggerCollision(state, eid, other);
      const activePairs = getActiveCollisionPairs(state);
      const alreadyTracked = activePairs.get(eid)?.has(other) ?? false;

      addActiveCollisionPair(state, eid, other, trigger);

      if (!alreadyTracked) {
        _enteredPairs.add(collisionPairKey(eid, other));

        const resolved = resolveModule(state, eid);
        if (!resolved) continue;

        const ctx = buildContext(state, eid);
        _collisionOther.entity = other;
        if (trigger) {
          resolved.mod.onTriggerEnter?.(ctx, _collisionOther);
        } else {
          resolved.mod.onCollisionEnter?.(ctx, _collisionOther);
        }
      }
    }

    for (const eid of touchEndedWithScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1)
        continue;

      const other = TouchEndedEvent.other[eid];
      const pairs = getActiveCollisionPairs(state);
      const wasTrigger = pairs.get(eid)?.get(other) ?? false;
      removeActiveCollisionPair(state, eid, other);

      const resolved = resolveModule(state, eid);
      if (!resolved) continue;

      const ctx = buildContext(state, eid);
      _collisionOther.entity = other;
      if (wasTrigger) {
        resolved.mod.onTriggerExit?.(ctx, _collisionOther);
      } else {
        resolved.mod.onCollisionExit?.(ctx, _collisionOther);
      }
    }

    const activePairs = getActiveCollisionPairs(state);
    for (const [eid, others] of activePairs) {
      if (!state.exists(eid)) {
        activePairs.delete(eid);
        continue;
      }
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1)
        continue;

      const resolved = resolveModule(state, eid);
      if (!resolved) continue;

      const ctx = buildContext(state, eid);
      for (const [other, trigger] of others) {
        if (!state.exists(other)) {
          others.delete(other);
          continue;
        }
        if (_enteredPairs.has(collisionPairKey(eid, other))) continue;
        _collisionOther.entity = other;
        if (trigger) {
          resolved.mod.onTriggerStay?.(ctx, _collisionOther);
        } else {
          resolved.mod.onCollisionStay?.(ctx, _collisionOther);
        }
      }
      if (others.size === 0) {
        activePairs.delete(eid);
      }
    }
  },
};
