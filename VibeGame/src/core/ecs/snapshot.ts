import { defineQuery } from './query';
import { addComponent } from 'bitecs';
import { Parent } from './components';
import type { Component } from './types';
import type { State } from './state';
import type { World } from 'bitecs';

// Cache one query per component: defineQuery allocates a closure + per-world
// cache, so rebuilding it on every snapshot churns garbage for no benefit.
const componentQueries = new WeakMap<Component, (world: World) => number[]>();

function getComponentQuery(component: Component): (world: World) => number[] {
  let q = componentQueries.get(component);
  if (!q) {
    q = defineQuery([component]);
    componentQueries.set(component, q);
  }
  return q;
}

export interface SnapshotOptions {
  entities?: string[];
  components?: string[];
  includeSequences?: boolean;
}

export interface SequenceSnapshot {
  name: string;
  eid: number;
  state: 'idle' | 'playing';
  currentIndex: number;
  itemCount: number;
  progress: number;
}

export interface EntitySnapshot {
  eid: number;
  name?: string;
  components: Record<string, Record<string, number>>;
}

export interface WorldSnapshot {
  elapsed: number;
  entities: EntitySnapshot[];
  sequences?: SequenceSnapshot[];
}

type ComponentField =
  Float32Array | Int32Array | Uint8Array | Uint16Array | Uint32Array;

function getComponentFields(
  component: Component,
  eid: number
): Record<string, number> {
  const fields: Record<string, number> = {};
  for (const key in component) {
    if (key.startsWith('_')) continue;
    const field = component[key as keyof Component] as ComponentField | unknown;
    if (
      field instanceof Float32Array ||
      field instanceof Int32Array ||
      field instanceof Uint8Array ||
      field instanceof Uint16Array ||
      field instanceof Uint32Array
    ) {
      fields[key] = field[eid];
    }
  }
  return fields;
}

export function createSnapshot(
  state: State,
  options?: SnapshotOptions
): WorldSnapshot {
  const entityMap = new Map<number, EntitySnapshot>();
  const nameFilter = options?.entities ? new Set(options.entities) : null;
  const componentFilter = options?.components
    ? new Set(options.components)
    : null;

  const entityNames = state.getNamedEntities();
  const nameByEid = new Map<number, string>();
  for (const [name, eid] of entityNames) {
    nameByEid.set(eid, name);
  }

  for (const componentName of state.getComponentNames()) {
    if (componentFilter && !componentFilter.has(componentName)) continue;

    const component = state.getComponent(componentName);
    if (!component) continue;

    const query = getComponentQuery(component);
    const entities = query(state.world);

    for (const eid of entities) {
      const entityName = nameByEid.get(eid);

      if (nameFilter && (!entityName || !nameFilter.has(entityName))) continue;

      if (!entityMap.has(eid)) {
        entityMap.set(eid, {
          eid,
          name: entityName,
          components: {},
        });
      }

      const snapshot = entityMap.get(eid)!;
      snapshot.components[componentName] = getComponentFields(component, eid);
    }
  }

  const entities = Array.from(entityMap.values()).sort((a, b) => {
    if (a.name && b.name) return a.name.localeCompare(b.name);
    if (a.name) return -1;
    if (b.name) return 1;
    return a.eid - b.eid;
  });

  const result: WorldSnapshot = {
    elapsed: state.time.elapsed,
    entities,
  };

  if (options?.includeSequences) {
    const sequenceComponent = state.getComponent('sequence');
    if (sequenceComponent) {
      const query = getComponentQuery(sequenceComponent);
      const seqEntities = query(state.world);
      const sequences: SequenceSnapshot[] = [];

      for (const eid of seqEntities) {
        const fields = getComponentFields(sequenceComponent, eid);
        const itemCount = fields.itemCount ?? 0;
        sequences.push({
          name: nameByEid.get(eid) ?? `eid-${eid}`,
          eid,
          state: fields.state === 1 ? 'playing' : 'idle',
          currentIndex: fields.currentIndex ?? 0,
          itemCount,
          progress: itemCount > 0 ? (fields.currentIndex ?? 0) / itemCount : 0,
        });
      }

      result.sequences = sequences.sort((a, b) => a.name.localeCompare(b.name));
    }
  }

  return result;
}

function formatComponentValue(
  componentName: string,
  fields: Record<string, number>
): string {
  const keys = Object.keys(fields);
  if (keys.length === 0) return `${componentName}: (empty)`;

  if (
    componentName === 'transform' &&
    'posX' in fields &&
    'rotX' in fields &&
    'scaleX' in fields
  ) {
    const pos = `pos(${fields.posX.toFixed(2)}, ${fields.posY.toFixed(2)}, ${fields.posZ.toFixed(2)})`;
    const rot = `rot(${fields.eulerX.toFixed(2)}, ${fields.eulerY.toFixed(2)}, ${fields.eulerZ.toFixed(2)})`;
    const scale = `scale(${fields.scaleX.toFixed(2)}, ${fields.scaleY.toFixed(2)}, ${fields.scaleZ.toFixed(2)})`;
    return `${componentName}: ${pos} ${rot} ${scale}`;
  }

  const pairs = keys.map((k) => {
    const v = fields[k];
    return `${k}=${typeof v === 'number' && !Number.isInteger(v) ? v.toFixed(2) : v}`;
  });
  return `${componentName}: ${pairs.join(' ')}`;
}

export function formatSnapshot(snapshot: WorldSnapshot): string {
  const lines: string[] = [];
  lines.push(`=== elapsed: ${snapshot.elapsed.toFixed(2)}s ===`);
  lines.push('');

  for (const entity of snapshot.entities) {
    const label = entity.name ? `[${entity.name}]` : `[eid=${entity.eid}]`;
    lines.push(`${label} eid=${entity.eid}`);

    for (const [componentName, fields] of Object.entries(entity.components)) {
      lines.push(`  ${formatComponentValue(componentName, fields)}`);
    }
    lines.push('');
  }

  return lines.join('\n');
}

export interface RestoreResult {
  oldToNewEid: Map<number, number>;
  restoredCount: number;
  skippedComponents: string[];
}

export function restoreSnapshot(
  state: State,
  snapshot: WorldSnapshot
): RestoreResult {
  const oldToNewEid = new Map<number, number>();
  const skippedComponents: string[] = [];

  state.time.elapsed = snapshot.elapsed ?? 0;

  for (const ent of snapshot.entities) {
    const newEid = state.createEntity();
    oldToNewEid.set(ent.eid, newEid);

    if (ent.name) {
      state.setEntityName(ent.name, newEid);
    }

    for (const [compName, fields] of Object.entries(ent.components)) {
      if (compName === 'parent') continue;
      const comp = state.getComponent(compName);
      if (!comp) {
        if (!skippedComponents.includes(compName)) {
          skippedComponents.push(compName);
        }
        continue;
      }
      state.addComponent(newEid, comp, fields);
    }
  }

  for (const ent of snapshot.entities) {
    const parentFields = ent.components['parent'];
    if (!parentFields || typeof parentFields.entity !== 'number') continue;
    const newEid = oldToNewEid.get(ent.eid);
    const newParent = oldToNewEid.get(parentFields.entity);
    if (newEid === undefined || newParent === undefined) continue;
    addComponent(state.world, newEid, Parent);
    Parent.entity[newEid] = newParent;
  }

  return {
    oldToNewEid,
    restoredCount: snapshot.entities.length,
    skippedComponents,
  };
}
