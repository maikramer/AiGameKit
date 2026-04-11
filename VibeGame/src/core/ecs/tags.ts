import { defineComponent, Types } from 'bitecs';

export const Tag = defineComponent({
  value: Types.ui8,
});

const tagById = new Map<number, string>();
const tagByName = new Map<string, number>();
let nextTagId = 0;

function registerBuiltin(name: string, id: number): void {
  tagById.set(id, name);
  tagByName.set(name, id);
  if (id >= nextTagId) {
    nextTagId = id + 1;
  }
}

registerBuiltin('Untagged', 0);
registerBuiltin('Player', 1);
registerBuiltin('MainCamera', 2);
registerBuiltin('Respawn', 3);
registerBuiltin('Finish', 4);
registerBuiltin('EditorOnly', 5);

export function addTag(name: string): number {
  const existing = tagByName.get(name);
  if (existing !== undefined) return existing;
  const id = nextTagId++;
  if (id > 255) {
    throw new Error(`[VibeGame] Tag ID overflow: maximum 256 tags (ui8)`);
  }
  tagById.set(id, name);
  tagByName.set(name, id);
  return id;
}

export function getTagId(name: string): number {
  return tagByName.get(name) ?? -1;
}

export function getTagName(id: number): string {
  return tagById.get(id) ?? '';
}
