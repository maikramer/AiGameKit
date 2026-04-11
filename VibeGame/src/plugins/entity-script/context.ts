import type { State } from '../../core';

import type { EntityScriptModule } from './types';

const scriptFileByState = new WeakMap<State, Map<number, string>>();
const globByState = new WeakMap<
  State,
  Record<string, () => Promise<unknown>>
>();
const setupInflightByState = new WeakMap<State, Set<number>>();
/** Resolved modules keyed by glob path (e.g. `./scripts/cristal.ts`). */
const moduleByGlobKey = new WeakMap<State, Map<string, EntityScriptModule>>();
const moduleLoadPromises = new WeakMap<
  State,
  Map<string, Promise<EntityScriptModule | null>>
>();
/** Tracks previous enabled state per entity for onEnable/onDisable transitions. */
const prevEnabledByState = new WeakMap<State, Map<number, number>>();

/** Active collision pairs per entity: entity → (other → isTrigger). */
const activeCollisionPairsByState = new WeakMap<State, Map<number, Map<number, boolean>>>();

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

  const keys = Object.keys(glob);
  const matches = keys.filter((key) => {
    const base = key.split('/').pop() ?? key;
    return base === f || key.endsWith(`/${f}`);
  });

  if (matches.length === 0) {
    return undefined;
  }
  if (matches.length > 1) {
    console.warn(
      `[entity-script] Ambiguous glob match for "${file}": ${matches.join(', ')}. Using first.`
    );
  }
  return matches[0];
}

export function getCachedEntityScriptModule(
  state: State,
  globKey: string
): EntityScriptModule | undefined {
  return moduleByGlobKey.get(state)?.get(globKey);
}

export function setCachedEntityScriptModule(
  state: State,
  globKey: string,
  mod: EntityScriptModule
): void {
  let m = moduleByGlobKey.get(state);
  if (!m) {
    m = new Map();
    moduleByGlobKey.set(state, m);
  }
  m.set(globKey, mod);
}

export function getPrevEnabled(state: State, entity: number): number | undefined {
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

export function getActiveCollisionPairs(state: State): Map<number, Map<number, boolean>> {
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
  isTrigger: boolean,
): void {
  const pairs = getActiveCollisionPairs(state);
  let entityPairs = pairs.get(entity);
  if (!entityPairs) {
    entityPairs = new Map();
    pairs.set(entity, entityPairs);
  }
  entityPairs.set(other, isTrigger);
}

export function removeActiveCollisionPair(state: State, entity: number, other: number): boolean {
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

export function deleteActiveCollisionPairsForEntity(state: State, entity: number): void {
  activeCollisionPairsByState.get(state)?.delete(entity);
}

export function getOrLoadEntityScriptModule(
  state: State,
  glob: Record<string, () => Promise<unknown>>,
  globKey: string
): Promise<EntityScriptModule | null> {
  const cached = getCachedEntityScriptModule(state, globKey);
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
    .then((raw) => coerceEntityScriptModule(raw))
    .then((mod) => {
      if (mod) {
        setCachedEntityScriptModule(state, globKey, mod);
      }
      byKey!.delete(globKey);
      return mod;
    })
    .catch((err: unknown) => {
      byKey!.delete(globKey);
      throw err;
    });

  byKey.set(globKey, p);
  return p;
}

export function coerceEntityScriptModule(
  m: unknown
): EntityScriptModule | null {
  if (typeof m !== 'object' || m === null) {
    return null;
  }
  const o = m as Record<string, unknown>;
  const awake =
    typeof o.awake === 'function'
      ? (o.awake as EntityScriptModule['awake'])
      : undefined;
  const onEnable =
    typeof o.onEnable === 'function'
      ? (o.onEnable as EntityScriptModule['onEnable'])
      : undefined;
  const onDisable =
    typeof o.onDisable === 'function'
      ? (o.onDisable as EntityScriptModule['onDisable'])
      : undefined;
  const start =
    typeof o.start === 'function'
      ? (o.start as EntityScriptModule['start'])
      : undefined;
  const update =
    typeof o.update === 'function'
      ? (o.update as EntityScriptModule['update'])
      : undefined;
  const onDestroy =
    typeof o.onDestroy === 'function'
      ? (o.onDestroy as EntityScriptModule['onDestroy'])
      : undefined;
  const fixedUpdate =
    typeof o.fixedUpdate === 'function'
      ? (o.fixedUpdate as EntityScriptModule['fixedUpdate'])
      : undefined;
  const lateUpdate =
    typeof o.lateUpdate === 'function'
      ? (o.lateUpdate as EntityScriptModule['lateUpdate'])
      : undefined;
  const onCollisionEnter =
    typeof o.onCollisionEnter === 'function'
      ? (o.onCollisionEnter as EntityScriptModule['onCollisionEnter'])
      : undefined;
  const onCollisionStay =
    typeof o.onCollisionStay === 'function'
      ? (o.onCollisionStay as EntityScriptModule['onCollisionStay'])
      : undefined;
  const onCollisionExit =
    typeof o.onCollisionExit === 'function'
      ? (o.onCollisionExit as EntityScriptModule['onCollisionExit'])
      : undefined;
  const onTriggerEnter =
    typeof o.onTriggerEnter === 'function'
      ? (o.onTriggerEnter as EntityScriptModule['onTriggerEnter'])
      : undefined;
  const onTriggerStay =
    typeof o.onTriggerStay === 'function'
      ? (o.onTriggerStay as EntityScriptModule['onTriggerStay'])
      : undefined;
  const onTriggerExit =
    typeof o.onTriggerExit === 'function'
      ? (o.onTriggerExit as EntityScriptModule['onTriggerExit'])
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
