import { logger } from '../../core/utils/logger';
import type { Component } from '../../core';

import {
  defineSystem,
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
import { DistanceCull } from '../rendering/components';
import { Transform } from '../transforms/components';
import { GltfPending } from '../gltf-xml/components';
import { getGltfRootGroup } from '../gltf-xml/group-registry';
import { MonoBehaviour } from './components';
import {
  addActiveCollisionPair,
  deleteEntityScriptCleanupRegistered,
  deletePrevEnabled,
  deleteScriptFile,
  deleteScriptRuntime,
  getActiveCollisionPairs,
  getCachedMonoBehaviourModule,
  getEntityScriptsGlob,
  getOrLoadMonoBehaviourModule,
  getPrevEnabled,
  getScriptFile,
  getScriptRuntime,
  isEntityScriptSetupInflight,
  markEntityScriptCleanupRegistered,
  removeActiveCollisionPair,
  resolveEntityScriptGlobKey,
  scriptLoadRetryGate,
  setEntityScriptSetupInflight,
  setPrevEnabled,
  setScriptRuntime,
} from './context';
import {
  beginScriptProfilePass,
  endScriptProfilePass,
  profileScriptCall,
} from './script-profiler';
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
  const component = state.getComponent(name);
  if (!component) return null;
  if (state.hasComponent(eid, component)) return component;

  // Snapshot the query once. `parentQuery` returns a per-query scratch array
  // that the next call overwrites, so recursing (the previous shape) walked a
  // buffer a deeper frame had already re-filled. The copy also makes the
  // descendant walk immune to entities being destroyed mid-walk, and the
  // `seen` set stops a malformed Parent cycle from hanging the frame.
  const withParent = parentQuery(state.world).slice();
  const frontier: number[] = [eid];
  const seen = new Set<number>([eid]);
  while (frontier.length > 0) {
    const parent = frontier.pop() as number;
    for (let i = 0; i < withParent.length; i++) {
      const child = withParent[i] as number;
      if (Parent.entity[child] !== parent || seen.has(child)) continue;
      if (state.hasComponent(child, component)) return component;
      seen.add(child);
      frontier.push(child);
    }
  }
  return null;
}

function findComponentInParent(
  state: State,
  eid: number,
  name: string
): Component | null {
  const component = state.getComponent(name);
  if (!component) return null;

  // Iterative walk with a cycle guard: a Parent loop used to recurse until the
  // stack blew up.
  let current = eid;
  const seen = new Set<number>();
  while (!seen.has(current)) {
    if (state.hasComponent(current, component)) return component;
    seen.add(current);
    if (!state.hasComponent(current, Parent)) return null;
    const parentEid = Parent.entity[current];
    if (parentEid === 0) return null;
    current = parentEid;
  }
  return null;
}

function shouldWaitForGltf(state: State, eid: number): boolean {
  const GltfP = state.getComponent('gltf-pending');
  if (!GltfP || !state.hasComponent(eid, GltfP)) {
    return false;
  }
  return GltfPending.loaded[eid] === 0;
}

/**
 * Visual distance-cull also pauses gameplay scripts. Spawned props/enemies with
 * `max-distance` keep MonoBehaviour attached; running AI/anim while hidden is wasted.
 * Ground placement is owned by the spawner (`TerrainSpawned` / AABB), not by
 * keeping distant scripts awake.
 */
function isDistanceCulled(state: State, eid: number): boolean {
  // Typed-array read first: entities without DistanceCull read 0 and answer
  // "not culled" without paying for `hasComponent` (entityExists + bitecs
  // lookup) on every scripted entity, every frame. The membership check is
  // still required for the 1 case, where a recycled eid could carry a stale
  // flag from its previous owner.
  return (
    DistanceCull.culled[eid] === 1 && state.hasComponent(eid, DistanceCull)
  );
}

export const EntityScriptSystem: System = defineSystem({
  name: 'EntityScriptSystem',
  group: 'simulation',
  update(state: State): void {
    if (state.headless) return;

    const glob = getEntityScriptsGlob(state);
    const profiling = beginScriptProfilePass(state.time.frameCount);

    for (const eid of entityScriptQuery(state.world)) {
      // Setup (ready===0) ainda corre para spawns distantes; updates quentes
      // saltam. Check barato primeiro (evita lookups de side-table para
      // milhares de scripts culled por frame).
      if (MonoBehaviour.ready[eid] === 1 && isDistanceCulled(state, eid)) {
        continue;
      }

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

        // Transient load failures (dev-server restart / dep re-optimization)
        // retry with backoff; only a module that burned all attempts is
        // permanently skipped.
        const gate = scriptLoadRetryGate(state, globKey, state.time.elapsed);
        if (gate === 'cooldown') {
          continue;
        }
        if (gate === 'exhausted') {
          MonoBehaviour.ready[eid] = 1;
          continue;
        }

        if (isEntityScriptSetupInflight(state, eid)) {
          continue;
        }

        setEntityScriptSetupInflight(state, eid, true);
        // Register cleanup BEFORE the async load: a destroy while the module
        // loads or `start()` awaits would otherwise never run onDisable/
        // onDestroy and would leak the scriptFile side-table entry (the old
        // registration happened only after the load completed). Load retries
        // re-enter this block — register the callback only on the first one;
        // on a retry success the module still resolves via the glob-key cache.
        let loadedModule: MonoBehaviourModule | undefined;
        let loadedCtx: MonoBehaviourContext | undefined;
        if (!markEntityScriptCleanupRegistered(state, eid)) {
          state.onDestroy(eid, () => {
            deleteEntityScriptCleanupRegistered(state, eid);
            const mod =
              loadedModule ?? getCachedMonoBehaviourModule(state, globKey);
            if (mod) {
              const destroyCtx = loadedCtx ?? buildContext(state, eid);
              if (MonoBehaviour.enabled[eid] === 1 && mod.onDisable) {
                mod.onDisable(destroyCtx);
              }
              if (mod.onDestroy) {
                mod.onDestroy(destroyCtx);
              }
            }
            deletePrevEnabled(state, eid);
            deleteScriptRuntime(state, eid);
            deleteScriptFile(state, eid);
          });
        }
        void (async () => {
          let mod: MonoBehaviourModule | null;
          try {
            mod = await getOrLoadMonoBehaviourModule(state, glob, globKey);
          } catch {
            // Load failure — already logged + recorded by the module loader
            // (shared per glob key). Leave ready=0: the retry gate re-attempts
            // with backoff, or latches exhausted on a later setup pass.
            setEntityScriptSetupInflight(state, eid, false);
            return;
          }
          try {
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
            loadedModule = mod;
            loadedCtx = ctx;
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
              // Runtime em cache: o loop por frame usa ctx+mod sem lookups.
              setScriptRuntime(state, eid, { mod, ctx, file });
            }
          } catch (err) {
            // The module loaded fine — the error is in the script's own
            // lifecycle (typically `start`). Latch ready so start side
            // effects are not re-run on every retry cycle.
            logger.error(`[entity-script] start failed for "${file}":`, err);
            if (state.exists(eid)) {
              MonoBehaviour.ready[eid] = 1;
            }
          }
          setEntityScriptSetupInflight(state, eid, false);
        })();
        continue;
      }

      if (MonoBehaviour.ready[eid] !== 1) {
        continue;
      }

      // Runtime resolvido no setup: 1 lookup + chamada direta por frame (sem
      // glob/globKey/módulo/`buildContext` — milhares de scripts de spawner).
      const rt = getScriptRuntime(state, eid);
      if (!rt) {
        continue;
      }
      const mod = rt.mod;

      const curEnabled = MonoBehaviour.enabled[eid];
      const prev = getPrevEnabled(state, eid);

      if (prev !== undefined && curEnabled !== prev) {
        if (prev === 1 && curEnabled === 0 && mod.onDisable) {
          mod.onDisable(rt.ctx);
        } else if (prev === 0 && curEnabled === 1 && mod.onEnable) {
          mod.onEnable(rt.ctx);
        }
        setPrevEnabled(state, eid, curEnabled);
      }

      if (curEnabled !== 1) {
        continue;
      }

      if (!mod.update) {
        continue;
      }

      rt.ctx.deltaTime = state.time.deltaTime;
      profileScriptCall(profiling, rt.file, 'update', () => {
        mod.update!(rt.ctx);
      });
    }

    if (profiling) endScriptProfilePass();
  },
});

function resolveModule(
  state: State,
  eid: number
): { mod: MonoBehaviourModule; file: string } | null {
  const file = getScriptFile(state, eid);
  if (!file) return null;

  const glob = getEntityScriptsGlob(state);
  if (!glob) return null;

  const globKey = resolveEntityScriptGlobKey(glob, file);
  if (!globKey) return null;

  const mod = getCachedMonoBehaviourModule(state, globKey);
  if (!mod) return null;

  return { mod, file };
}

export const EntityScriptFixedUpdateSystem: System = defineSystem({
  name: 'EntityScriptFixedUpdateSystem',
  group: 'fixed',
  update(state: State): void {
    if (state.headless) return;

    const profiling = beginScriptProfilePass(state.time.frameCount);

    for (const eid of entityScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1) {
        continue;
      }
      if (isDistanceCulled(state, eid)) continue;

      // Runtime em cache: 1 lookup (sem re-resolver glob/módulo por frame).
      const rt = getScriptRuntime(state, eid);
      if (!rt || !rt.mod.fixedUpdate) {
        continue;
      }

      profileScriptCall(profiling, rt.file, 'fixed', () => {
        rt.mod.fixedUpdate!(rt.ctx);
      });
    }

    if (profiling) endScriptProfilePass();
  },
});

export const EntityScriptLateUpdateSystem: System = defineSystem({
  name: 'EntityScriptLateUpdateSystem',
  group: 'late',
  update(state: State): void {
    if (state.headless) return;

    const profiling = beginScriptProfilePass(state.time.frameCount);

    for (const eid of entityScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1) {
        continue;
      }
      if (isDistanceCulled(state, eid)) continue;

      // Runtime em cache: 1 lookup (sem re-resolver glob/módulo por frame).
      const rt = getScriptRuntime(state, eid);
      if (!rt || !rt.mod.lateUpdate) {
        continue;
      }

      profileScriptCall(profiling, rt.file, 'late', () => {
        rt.mod.lateUpdate!(rt.ctx);
      });
    }

    if (profiling) endScriptProfilePass();
  },
});

function isTriggerCollision(state: State, eid1: number, eid2: number): boolean {
  const hasC1 = state.hasComponent(eid1, Collider);
  const hasC2 = state.hasComponent(eid2, Collider);
  if (hasC1 && Collider.isSensor[eid1] === 1) return true;
  if (hasC2 && Collider.isSensor[eid2] === 1) return true;
  return false;
}

export const EntityScriptCollisionBridgeSystem: System = defineSystem({
  name: 'EntityScriptCollisionBridgeSystem',
  group: 'simulation',
  update(state: State): void {
    if (state.headless) return;

    const profiling = beginScriptProfilePass(state.time.frameCount);
    _enteredPairs.clear();

    for (const eid of touchedWithScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1)
        continue;
      if (isDistanceCulled(state, eid)) continue;

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
        profileScriptCall(profiling, resolved.file, 'collision', () => {
          if (trigger) {
            resolved.mod.onTriggerEnter?.(ctx, _collisionOther);
          } else {
            resolved.mod.onCollisionEnter?.(ctx, _collisionOther);
          }
        });
      }
    }

    for (const eid of touchEndedWithScriptQuery(state.world)) {
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1)
        continue;
      if (isDistanceCulled(state, eid)) continue;

      const other = TouchEndedEvent.other[eid];
      const pairs = getActiveCollisionPairs(state);
      const wasTrigger = pairs.get(eid)?.get(other) ?? false;
      removeActiveCollisionPair(state, eid, other);

      const resolved = resolveModule(state, eid);
      if (!resolved) continue;

      const ctx = buildContext(state, eid);
      _collisionOther.entity = other;
      profileScriptCall(profiling, resolved.file, 'collision', () => {
        if (wasTrigger) {
          resolved.mod.onTriggerExit?.(ctx, _collisionOther);
        } else {
          resolved.mod.onCollisionExit?.(ctx, _collisionOther);
        }
      });
    }

    const activePairs = getActiveCollisionPairs(state);
    for (const [eid, others] of activePairs) {
      if (!state.exists(eid)) {
        activePairs.delete(eid);
        continue;
      }
      if (MonoBehaviour.ready[eid] !== 1 || MonoBehaviour.enabled[eid] !== 1)
        continue;
      if (isDistanceCulled(state, eid)) continue;

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
        profileScriptCall(profiling, resolved.file, 'collision', () => {
          if (trigger) {
            resolved.mod.onTriggerStay?.(ctx, _collisionOther);
          } else {
            resolved.mod.onCollisionStay?.(ctx, _collisionOther);
          }
        });
      }
      if (others.size === 0) {
        activePairs.delete(eid);
      }
    }

    if (profiling) endScriptProfilePass();
  },
});
