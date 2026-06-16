// Generic (kind, id) data registry for data-driven RPG plugins. Loads from
// YAML/JSON text or a directory of `.yaml`/`.yml`/`.json` files.
//
// Document shape: `{ <kind>: { <id>: <def> } }`. The top-level key is used
// verbatim as the registry kind (prefer singular names: `item`, `skill`,
// `statusEffect`). Each definition gets an injected `id` field equal to its
// key when it does not already declare one.

import { parse as parseYaml } from 'yaml';
import { Glob } from 'bun';
import type { State } from '../../core';

type DefMap = Map<string, unknown>;

const EMPTY_READONLY: readonly unknown[] = Object.freeze([]);

export class DataRegistry {
  private readonly store = new Map<string, DefMap>();

  register<T>(kind: string, id: string, def: T): void {
    let bucket = this.store.get(kind);
    if (!bucket) {
      bucket = new Map();
      this.store.set(kind, bucket);
    }
    bucket.set(id, def);
  }

  get<T>(kind: string, id: string): T | undefined {
    return this.store.get(kind)?.get(id) as T | undefined;
  }

  all<T>(kind: string): readonly T[] {
    const bucket = this.store.get(kind);
    if (!bucket || bucket.size === 0) return EMPTY_READONLY as readonly T[];
    return Array.from(bucket.values()) as readonly T[];
  }

  has(kind: string, id: string): boolean {
    return this.store.get(kind)?.has(id) ?? false;
  }

  kinds(): readonly string[] {
    return Array.from(this.store.keys());
  }

  clear(): void {
    this.store.clear();
  }

  loadYaml(yamlText: string): void {
    let doc: unknown;
    try {
      doc = parseYaml(yamlText);
    } catch (err) {
      throw new Error(
        `Failed to parse YAML: ${(err as Error).message ?? err}`
      );
    }
    // `parseYaml` returns `null` for an empty document — treat as no-op.
    if (doc === null || doc === undefined) return;
    this.ingest(doc);
  }

  loadJson(jsonText: string): void {
    let doc: unknown;
    try {
      doc = JSON.parse(jsonText);
    } catch (err) {
      throw new Error(
        `Failed to parse JSON: ${(err as Error).message ?? err}`
      );
    }
    this.ingest(doc);
  }

  /**
   * Read every `.yaml`/`.yml`/`.json` file directly under `dir` (non-recursive)
   * and register its contents. Uses `Bun.file` for file I/O.
   */
  async loadDirectory(dir: string): Promise<void> {
    const glob = new Glob('*.{yaml,yml,json}');
    const paths: string[] = [];
    for await (const match of glob.scan({ cwd: dir, onlyFiles: true })) {
      paths.push(match);
    }
    paths.sort();
    for (const rel of paths) {
      const text = await Bun.file(`${dir}/${rel}`).text();
      if (rel.endsWith('.json')) {
        this.loadJson(text);
      } else {
        this.loadYaml(text);
      }
    }
  }

  private ingest(doc: unknown): void {
    if (typeof doc !== 'object' || doc === null || Array.isArray(doc)) {
      throw new Error(
        'DataRegistry: top-level document must be an object ' +
          '{ <kind>: { <id>: <def> } }'
      );
    }
    const root = doc as Record<string, unknown>;
    for (const [kind, entries] of Object.entries(root)) {
      if (typeof kind !== 'string' || kind.length === 0) continue;
      if (
        entries === null ||
        typeof entries !== 'object' ||
        Array.isArray(entries)
      ) {
        console.warn(
          `[DataRegistry] skipping kind "${kind}": expected an object of definitions`
        );
        continue;
      }
      for (const [id, def] of Object.entries(entries as Record<
        string,
        unknown
      >)) {
        if (def === null || typeof def !== 'object' || Array.isArray(def)) {
          console.warn(
            `[DataRegistry] skipping ${kind}/${id}: definition must be an object`
          );
          continue;
        }
        const normalized = { ...(def as Record<string, unknown>) };
        if (normalized.id === undefined) normalized.id = id;
        this.register(kind, id, normalized);
      }
    }
  }
}

const registries = new WeakMap<State, DataRegistry>();

export function getDataRegistry(state: State): DataRegistry {
  let reg = registries.get(state);
  if (!reg) {
    reg = new DataRegistry();
    registries.set(state, reg);
  }
  return reg;
}
