import type { State } from '../../core';

export interface RegisterDebugActionOptions {
  description?: string;
}

type DebugActionFn = (...args: unknown[]) => unknown;

export interface DebugActionEntry {
  name: string;
  fn: DebugActionFn;
  description?: string;
}

export interface DebugVarEntry {
  name: string;
  get: () => unknown;
  set?: (value: unknown) => void;
}

export interface DebugRegistry {
  readonly actions: Map<string, DebugActionEntry>;
  readonly vars: Map<string, DebugVarEntry>;
}

export interface DebugRegistryHandle {
  actionNames(): string[];
  varNames(): string[];
  callAction(name: string, ...args: unknown[]): unknown;
  getVar(name: string): unknown;
  setVar(name: string, value: unknown): boolean;
  hasAction(name: string): boolean;
  hasVar(name: string): boolean;
}

const registries = new WeakMap<State, DebugRegistry>();

export function getDebugRegistry(state: State): DebugRegistry {
  let reg = registries.get(state);
  if (!reg) {
    reg = { actions: new Map(), vars: new Map() };
    registries.set(state, reg);
  }
  return reg;
}

export function getDebugRegistryHandle(state: State): DebugRegistryHandle {
  const reg = getDebugRegistry(state);
  return {
    actionNames() {
      return Array.from(reg.actions.keys());
    },
    varNames() {
      return Array.from(reg.vars.keys());
    },
    hasAction(name) {
      return reg.actions.has(name);
    },
    hasVar(name) {
      return reg.vars.has(name);
    },
    callAction(name, ...args) {
      const entry = reg.actions.get(name);
      if (!entry) return undefined;
      return entry.fn(...args);
    },
    getVar(name) {
      const entry = reg.vars.get(name);
      if (!entry) return undefined;
      return entry.get();
    },
    setVar(name, value) {
      const entry = reg.vars.get(name);
      if (!entry || !entry.set) return false;
      entry.set(value);
      return true;
    },
  };
}

/**
 * Register a callable debug action (e.g. a REPL command or overlay button).
 *
 * DEV-only by construction: in production builds (`import.meta.env.DEV === false`)
 * this is a no-op, so debug actions never ship.
 */
export function registerDebugAction<T extends unknown[] = unknown[]>(
  state: State,
  name: string,
  fn: (...args: T) => unknown,
  opts?: RegisterDebugActionOptions
): void {
  if (import.meta.env.DEV === false) return;
  const reg = getDebugRegistry(state);
  reg.actions.set(name, {
    name,
    fn: fn as DebugActionFn,
    description: opts?.description,
  });
}

/**
 * Register an inspectable (and optionally mutable) debug variable.
 *
 * DEV-only by construction: in production builds (`import.meta.env.DEV === false`)
 * this is a no-op, so debug variables never ship.
 */
export function registerDebugVar(
  state: State,
  name: string,
  getter: () => unknown,
  setter?: (value: unknown) => void
): void {
  if (import.meta.env.DEV === false) return;
  const reg = getDebugRegistry(state);
  reg.vars.set(name, { name, get: getter, set: setter });
}
