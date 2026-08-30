import { logger } from '../../core/utils/logger';
import type { State } from '../../core';

import type { MonoBehaviourContext, MonoBehaviourModule } from './types';

const scriptFileByState = new WeakMap<State, Map<number, string>>();
const globByState = new WeakMap<
  State,
  Record<string, () => Promise<unknown>>
>();
const setupInflightByState = new WeakMap<State, Set<number>>();
const cleanupRegisteredByState = new WeakMap<State, Set<number>>();
/** Resolved modules keyed by glob path (e.g. `./scripts/cristal.ts`). */
const moduleByGlobKey = new WeakMap<State, Map<string, MonoBehaviourModule>>();
const moduleLoadPromises = new WeakMap<
  State,
  Map<string, Promise<MonoBehaviourModule | null>>
>();
/** Runtime por entidade (módulo + ctx + ficheiro) — o loop por frame do
 * `EntityScriptSystem` faz 1 lookup + chamada direta, sem re-resolver glob,
 * módulo nem `buildContext` (milhares de scripts de spawner). */
const scriptRuntimeByState = new WeakMap<
  State,
  Map<number, EntityScriptRuntime>
>();

export interface EntityScriptRuntime {
  mod: MonoBehaviourModule;
  ctx: MonoBehaviourContext;
  file: string;
}
/** Tracks previous enabled state per entity for onEnable/onDisable transitions. */
const prevEnabledByState = new WeakMap<State, Map<number, number>>();

/**
 * Load-failure retry bookkeeping, shared per glob key: every entity pointing
 * at the same script backs off together instead of one import() per entity.
 * A dynamic import can fail transiently — the classic dev case is a Vite
 * dep re-optimization ("Outdated Optimize Dep", 504) right after a server
 * restart — and latching the entity as failed turned a 1-second hiccup into
 * a brainless enemy until a manual page reload.
 */
export interface ScriptLoadRetry {
  attempts: number;
  nextAttemptAt: number;
}

/** Give-up threshold after which the module is considered genuinely broken. */
export const SCRIPT_LOAD_MAX_ATTEMPTS = 5;

const scriptLoadRetriesByState = new WeakMap<
  State,
  Map<string, ScriptLoadRetry>
>();

export function getScriptLoadRetry(
  state: State,
  globKey: string
): ScriptLoadRetry | undefined {
  return scriptLoadRetriesByState.get(state)?.get(globKey);
}

export function setScriptLoadRetry(
  state: State,
  globKey: string,
  retry: ScriptLoadRetry
): void {
  let m = scriptLoadRetriesByState.get(state);
  if (!m) {
    m = new Map();
    scriptLoadRetriesByState.set(state, m);
  }
  m.set(globKey, retry);
}

export function deleteScriptLoadRetry(state: State, globKey: string): void {
  scriptLoadRetriesByState.get(state)?.delete(globKey);
}

export type ScriptLoadGate = 'load' | 'cooldown' | 'exhausted';

/**
 * May a new load attempt for `globKey` start now?
 *
 * - `load`: no failure on record, or the backoff expired and attempts remain.
 * - `cooldown`: a failed attempt is still backing off — skip this frame.
 * - `exhausted`: max attempts burned; the module is broken for this session.
 */
export function scriptLoadRetryGate(
  state: State,
  globKey: string,
  elapsed: number
): ScriptLoadGate {
  const retry = scriptLoadRetriesByState.get(state)?.get(globKey);
  if (!retry) return 'load';
  if (retry.attempts >= SCRIPT_LOAD_MAX_ATTEMPTS) return 'exhausted';
  if (elapsed < retry.nextAttemptAt) return 'cooldown';
  return 'load';
}

/** Record one failed load of `globKey` and log the shared outcome once. */
function noteScriptLoadFailure(
  state: State,
  globKey: string,
  err: unknown
): void {
  const prev = getScriptLoadRetry(state, globKey);
  const attempts = (prev?.attempts ?? 0) + 1;
  const backoff = Math.min(8, 0.5 * 2 ** (attempts - 1));
  setScriptLoadRetry(state, globKey, {
    attempts,
    nextAttemptAt: state.time.elapsed + backoff,
  });
  if (attempts >= SCRIPT_LOAD_MAX_ATTEMPTS) {
    logger.error(
      `[entity-script] Failed to load script module "${globKey}" after ${attempts} attempts — giving up for this session:`,
      err
    );
  } else {
    logger.warn(
      `[entity-script] Failed to load script module "${globKey}" (attempt ${attempts}/${SCRIPT_LOAD_MAX_ATTEMPTS}, retrying in ${backoff}s):`,
      err
    );
  }
}

/** Active collision pairs per entity: entity → (other → isTrigger). */
const activeCollisionPairsByState = new WeakMap<
  State,
  Map<number, Map<number, boolean>>
>();

export function setScriptFile(
  state: State,
  entity: number,
  file: string
): void {
  let m = scriptFileByState.get(state);
  if (!m) {
    m = new Map();
    scriptFileByState.set(state, m);
  }
  m.set(entity, file.trim());
}

export function getScriptFile(
  state: State,
  entity: number
): string | undefined {
  return scriptFileByState.get(state)?.get(entity);
}

export function deleteScriptFile(state: State, entity: number): void {
  scriptFileByState.get(state)?.delete(entity);
}

export function getScriptRuntime(
  state: State,
  entity: number
): EntityScriptRuntime | undefined {
  return scriptRuntimeByState.get(state)?.get(entity);
}

export function setScriptRuntime(
  state: State,
  entity: number,
  rt: EntityScriptRuntime
): void {
  let m = scriptRuntimeByState.get(state);
  if (!m) {
    m = new Map();
    scriptRuntimeByState.set(state, m);
  }
  m.set(entity, rt);
}

export function deleteScriptRuntime(state: State, entity: number): void {
  scriptRuntimeByState.get(state)?.delete(entity);
}

/**
 * Register the result of `import.meta.glob('./scripts/*.ts')` (or similar) for
 * resolving `script="file.ts"` on entities.
 */
export function registerEntityScripts(
  state: State,
  glob: Record<string, () => Promise<unknown>>
): void {
  globByState.set(state, glob);
}

export function getEntityScriptsGlob(
  state: State
): Record<string, () => Promise<unknown>> | undefined {
  return globByState.get(state);
}

export function isEntityScriptSetupInflight(
  state: State,
  entity: number
): boolean {
  return setupInflightByState.get(state)?.has(entity) ?? false;
}

/**
 * Mark the destroy-cleanup as registered for `entity`, returning whether it
 * was already registered. Load retries re-enter setup for the same entity;
 * without this guard each attempt would stack another `state.onDestroy`
 * callback and a post-retry destroy would fire `onDestroy` once per attempt.
 */
export function markEntityScriptCleanupRegistered(
  state: State,
  entity: number
): boolean {
  let s = cleanupRegisteredByState.get(state);
  if (!s) {
    s = new Set();
    cleanupRegisteredByState.set(state, s);
  }
  const had = s.has(entity);
  s.add(entity);
  return had;
}

export function deleteEntityScriptCleanupRegistered(
  state: State,
  entity: number
): void {
  cleanupRegisteredByState.get(state)?.delete(entity);
}

export function setEntityScriptSetupInflight(
  state: State,
  entity: number,
  v: boolean
): void {
  let s = setupInflightByState.get(state);
  if (!s) {
    s = new Set();
    setupInflightByState.set(state, s);
  }
  if (v) {
    s.add(entity);
  } else {
    s.delete(entity);
  }
}

const globKeyCacheByGlob = new WeakMap<
  Record<string, () => Promise<unknown>>,
  Map<string, string | undefined>
>();

/**
 * Resolve a module key from a glob map using a logical filename (e.g. `cristal.ts`).
 * Returns `undefined` if no unique match.
 */
export function resolveEntityScriptGlobKey(
  glob: Record<string, () => Promise<unknown>>,
  file: string
): string | undefined {
  const f = file.trim();
  if (!f) return undefined;

  let byFile = globKeyCacheByGlob.get(glob);
  if (!byFile) {
    byFile = new Map();
    globKeyCacheByGlob.set(glob, byFile);
  }
  if (byFile.has(f)) {
    return byFile.get(f);
  }

  const keys = Object.keys(glob);
  const matches = keys.filter((key) => {
    const base = key.split('/').pop() ?? key;
    return base === f || key.endsWith(`/${f}`);
  });

  let result: string | undefined;
  if (matches.length === 0) {
    result = undefined;
  } else {
    if (matches.length > 1) {
      logger.warn(
        `[entity-script] Ambiguous glob match for "${file}": ${matches.join(', ')}. Using first.`
      );
    }
    result = matches[0];
  }
  byFile.set(f, result);
  return result;
}

export function getCachedMonoBehaviourModule(
  state: State,
  globKey: string
): MonoBehaviourModule | undefined {
  return moduleByGlobKey.get(state)?.get(globKey);
}

export function setCachedMonoBehaviourModule(
  state: State,
  globKey: string,
  mod: MonoBehaviourModule
): void {
  let m = moduleByGlobKey.get(state);
  if (!m) {
    m = new Map();
    moduleByGlobKey.set(state, m);
  }
  m.set(globKey, mod);
}

export function getPrevEnabled(
  state: State,
  entity: number
): number | undefined {
  return prevEnabledByState.get(state)?.get(entity);
}

export function setPrevEnabled(state: State, entity: number, v: number): void {
  let m = prevEnabledByState.get(state);
  if (!m) {
    m = new Map();
    prevEnabledByState.set(state, m);
  }
  m.set(entity, v);
}

export function deletePrevEnabled(state: State, entity: number): void {
  prevEnabledByState.get(state)?.delete(entity);
}

export function getActiveCollisionPairs(
  state: State
): Map<number, Map<number, boolean>> {
  let pairs = activeCollisionPairsByState.get(state);
  if (!pairs) {
    pairs = new Map();
    activeCollisionPairsByState.set(state, pairs);
  }
  return pairs;
}

export function addActiveCollisionPair(
  state: State,
  entity: number,
  other: number,
  isTrigger: boolean
): void {
  const pairs = getActiveCollisionPairs(state);
  let entityPairs = pairs.get(entity);
  if (!entityPairs) {
    entityPairs = new Map();
    pairs.set(entity, entityPairs);
  }
  entityPairs.set(other, isTrigger);
}

export function removeActiveCollisionPair(
  state: State,
  entity: number,
  other: number
): boolean {
  const pairs = activeCollisionPairsByState.get(state);
  if (!pairs) return false;
  const entityPairs = pairs.get(entity);
  if (!entityPairs) return false;
  const had = entityPairs.delete(other);
  if (entityPairs.size === 0) {
    pairs.delete(entity);
  }
  return had;
}

export function deleteActiveCollisionPairsForEntity(
  state: State,
  entity: number
): void {
  activeCollisionPairsByState.get(state)?.delete(entity);
}

export function getOrLoadMonoBehaviourModule(
  state: State,
  glob: Record<string, () => Promise<unknown>>,
  globKey: string
): Promise<MonoBehaviourModule | null> {
  const cached = getCachedMonoBehaviourModule(state, globKey);
  if (cached) {
    return Promise.resolve(cached);
  }

  let byKey = moduleLoadPromises.get(state);
  if (!byKey) {
    byKey = new Map();
    moduleLoadPromises.set(state, byKey);
  }
  const existing = byKey.get(globKey);
  if (existing) {
    return existing;
  }

  const loader = glob[globKey];
  if (!loader) {
    return Promise.resolve(null);
  }

  const p = loader()
    .then((raw) => coerceMonoBehaviourModule(raw))
    .then((mod) => {
      if (mod) {
        setCachedMonoBehaviourModule(state, globKey, mod);
        // A previously failing module that now loads clears its backoff.
        deleteScriptLoadRetry(state, globKey);
      }
      byKey!.delete(globKey);
      return mod;
    })
    .catch((err: unknown) => {
      byKey!.delete(globKey);
      noteScriptLoadFailure(state, globKey, err);
      throw err;
    });

  byKey.set(globKey, p);
  return p;
}

export function coerceMonoBehaviourModule(
  m: unknown
): MonoBehaviourModule | null {
  if (typeof m !== 'object' || m === null) {
    return null;
  }
  const o = m as Record<string, unknown>;
  const awake =
    typeof o.awake === 'function'
      ? (o.awake as MonoBehaviourModule['awake'])
      : undefined;
  const onEnable =
    typeof o.onEnable === 'function'
      ? (o.onEnable as MonoBehaviourModule['onEnable'])
      : undefined;
  const onDisable =
    typeof o.onDisable === 'function'
      ? (o.onDisable as MonoBehaviourModule['onDisable'])
      : undefined;
  const start =
    typeof o.start === 'function'
      ? (o.start as MonoBehaviourModule['start'])
      : undefined;
  const update =
    typeof o.update === 'function'
      ? (o.update as MonoBehaviourModule['update'])
      : undefined;
  const onDestroy =
    typeof o.onDestroy === 'function'
      ? (o.onDestroy as MonoBehaviourModule['onDestroy'])
      : undefined;
  const fixedUpdate =
    typeof o.fixedUpdate === 'function'
      ? (o.fixedUpdate as MonoBehaviourModule['fixedUpdate'])
      : undefined;
  const lateUpdate =
    typeof o.lateUpdate === 'function'
      ? (o.lateUpdate as MonoBehaviourModule['lateUpdate'])
      : undefined;
  const onCollisionEnter =
    typeof o.onCollisionEnter === 'function'
      ? (o.onCollisionEnter as MonoBehaviourModule['onCollisionEnter'])
      : undefined;
  const onCollisionStay =
    typeof o.onCollisionStay === 'function'
      ? (o.onCollisionStay as MonoBehaviourModule['onCollisionStay'])
      : undefined;
  const onCollisionExit =
    typeof o.onCollisionExit === 'function'
      ? (o.onCollisionExit as MonoBehaviourModule['onCollisionExit'])
      : undefined;
  const onTriggerEnter =
    typeof o.onTriggerEnter === 'function'
      ? (o.onTriggerEnter as MonoBehaviourModule['onTriggerEnter'])
      : undefined;
  const onTriggerStay =
    typeof o.onTriggerStay === 'function'
      ? (o.onTriggerStay as MonoBehaviourModule['onTriggerStay'])
      : undefined;
  const onTriggerExit =
    typeof o.onTriggerExit === 'function'
      ? (o.onTriggerExit as MonoBehaviourModule['onTriggerExit'])
      : undefined;
  if (!start && !update) {
    return null;
  }
  return {
    awake,
    onEnable,
    onDisable,
    start,
    update,
    fixedUpdate,
    lateUpdate,
    onDestroy,
    onCollisionEnter,
    onCollisionStay,
    onCollisionExit,
    onTriggerEnter,
    onTriggerStay,
    onTriggerExit,
  };
}
