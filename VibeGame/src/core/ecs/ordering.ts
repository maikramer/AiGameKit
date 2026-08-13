import type { System } from './types';

export interface SystemOrderingError extends Error {
  readonly type: 'validation' | 'circular-dependency' | 'group-mismatch';
}

function createOrderingError(
  type: SystemOrderingError['type'],
  message: string
): SystemOrderingError {
  const error = new Error(message) as SystemOrderingError;
  (error as { type: SystemOrderingError['type'] }).type = type;
  return error;
}

/**
 * Resolves a `before`/`after` entry to a registered system.
 *
 * Entries may be the system object or its `name` — the name form lets a system
 * order itself against one it cannot import (plugin ↔ systems cycles). Both
 * forms resolve to `undefined` when the target is not registered, which callers
 * treat as "no constraint", the long-standing behaviour for object entries.
 *
 * Comparison is by object identity, so a raw string used to match nothing and
 * the constraint was dropped in silence.
 */
function resolveDependency(
  dep: System | string,
  systems: readonly System[]
): System | undefined {
  if (typeof dep === 'string') {
    return systems.find((candidate) => candidate.name === dep);
  }
  return systems.includes(dep) ? dep : undefined;
}

export function validateSystemConstraints(system: System): void {
  if (system.first && system.last) {
    throw createOrderingError(
      'validation',
      'System cannot have both first and last constraints'
    );
  }
}

export function validateGroupConstraints(
  system: System,
  allSystems: System[]
): void {
  const systemGroup = system.group ?? 'simulation';

  if (system.before) {
    for (const dep of system.before) {
      const beforeSystem = resolveDependency(dep, allSystems);
      if (!beforeSystem) continue;
      const beforeGroup = beforeSystem.group ?? 'simulation';
      if (beforeGroup !== systemGroup) {
        const from = system.name ?? '(unnamed)';
        const to = beforeSystem.name ?? '(unnamed)';
        throw createOrderingError(
          'group-mismatch',
          `System ${from} with before constraint references ${to} in different group (${systemGroup} vs ${beforeGroup})`
        );
      }
    }
  }

  if (system.after) {
    for (const dep of system.after) {
      const afterSystem = resolveDependency(dep, allSystems);
      if (!afterSystem) continue;
      const afterGroup = afterSystem.group ?? 'simulation';
      if (afterGroup !== systemGroup) {
        const from = system.name ?? '(unnamed)';
        const to = afterSystem.name ?? '(unnamed)';
        throw createOrderingError(
          'group-mismatch',
          `System ${from} with after constraint references ${to} in different group (${systemGroup} vs ${afterGroup})`
        );
      }
    }
  }
}

function buildDependencyGraph(systems: System[]): Map<System, Set<System>> {
  const graph = new Map<System, Set<System>>();

  for (const system of systems) {
    if (!graph.has(system)) {
      graph.set(system, new Set());
    }

    if (system.before) {
      for (const dep of system.before) {
        const beforeTarget = resolveDependency(dep, systems);
        if (!beforeTarget) continue;
        if (!graph.has(beforeTarget)) {
          graph.set(beforeTarget, new Set());
        }
        graph.get(system)!.add(beforeTarget);
      }
    }

    if (system.after) {
      for (const dep of system.after) {
        const afterTarget = resolveDependency(dep, systems);
        if (!afterTarget) continue;
        if (!graph.has(afterTarget)) {
          graph.set(afterTarget, new Set());
        }
        graph.get(afterTarget)!.add(system);
      }
    }
  }

  return graph;
}

function detectCycles(graph: Map<System, Set<System>>): void {
  const visited = new Set<System>();
  const stack = new Set<System>();

  function hasCycle(system: System): boolean {
    if (stack.has(system)) return true;
    if (visited.has(system)) return false;

    visited.add(system);
    stack.add(system);

    const deps = graph.get(system);
    if (deps?.size && [...deps].some(hasCycle)) return true;

    stack.delete(system);
    return false;
  }

  for (const system of graph.keys()) {
    if (hasCycle(system)) {
      throw createOrderingError(
        'circular-dependency',
        'Circular dependency detected in system constraints'
      );
    }
  }
}

function topologicalSort(systems: System[]): System[] {
  if (systems.length === 0) return [];

  const graph = buildDependencyGraph(systems);
  detectCycles(graph);

  const inDegree = new Map<System, number>();
  for (const system of systems) {
    inDegree.set(system, 0);
  }

  for (const deps of graph.values()) {
    for (const dep of deps) {
      inDegree.set(dep, (inDegree.get(dep) || 0) + 1);
    }
  }

  const queue: System[] = [];
  const sorted: System[] = [];

  for (const system of systems) {
    if (inDegree.get(system) === 0) {
      queue.push(system);
    }
  }

  while (queue.length > 0) {
    const system = queue.shift()!;
    sorted.push(system);

    const deps = graph.get(system) || new Set();
    for (const dep of deps) {
      const newDegree = (inDegree.get(dep) || 0) - 1;
      inDegree.set(dep, newDegree);
      if (newDegree === 0) {
        queue.push(dep);
      }
    }
  }

  return sorted;
}

export function sortSystemsByConstraints(
  systems: System[],
  _group: string,
  allSystems?: System[]
): System[] {
  const validation = allSystems || systems;
  systems.forEach((s) => {
    validateSystemConstraints(s);
    validateGroupConstraints(s, validation);
  });

  const categorized = systems.reduce(
    (acc, system) => {
      const key = system.first ? 'first' : system.last ? 'last' : 'normal';
      acc[key].push(system);
      return acc;
    },
    { first: [] as System[], normal: [] as System[], last: [] as System[] }
  );

  return [
    ...topologicalSort(categorized.first),
    ...topologicalSort(categorized.normal),
    ...topologicalSort(categorized.last),
  ];
}
