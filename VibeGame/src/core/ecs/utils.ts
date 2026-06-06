import type { Component } from './types';

type ComponentField =
  | Float32Array
  | Int32Array
  | Uint8Array
  | Uint16Array
  | Uint32Array;

export function setComponentFields(
  component: Component,
  entity: number,
  values: Record<string, number>
): void {
  for (const [key, value] of Object.entries(values)) {
    const field = component[key as keyof Component] as
      | ComponentField
      | undefined;
    if (field) {
      field[entity] = value;
    }
  }
}

/**
 * Zero every field of a component for one entity.
 *
 * Component stores are global typed arrays indexed by entity id, so a recycled
 * id (entity removed then a new one allocated to the same slot, or the same id
 * reused across worlds) carries stale data. Clearing on a fresh add — before
 * defaults and values are applied — guarantees a deterministic initial state,
 * regardless of whether the registered defaults cover every field.
 */
export function clearComponentFields(
  component: Component,
  entity: number
): void {
  for (const key in component) {
    const field = component[key as keyof Component] as
      | ComponentField
      | undefined;
    if (field && ArrayBuffer.isView(field)) {
      field[entity] = 0;
    }
  }
}
