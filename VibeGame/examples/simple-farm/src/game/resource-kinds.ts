// Extra `<ResourceNode kind="…">` values for this game.
//
// The engine ships wood=0, stone=1, ore=2. `resolveResourceNodeKind` falls back
// to 0 for anything it does not recognise, so a berry bush declared as
// `kind="food"` would silently harvest as WOOD — no error, no warning, just the
// wrong item in the bag. Registering the value is what makes the XML honest.
//
// `config.enums` merges per component but ASSIGNS the whole `kind` mapping, so
// the engine's three have to be repeated here or they disappear.
import type { Plugin } from 'aigamekit-vibegame';

export const RESOURCE_KINDS = {
  wood: 0,
  stone: 1,
  ore: 2,
  food: 3,
} as const;

export const FarmResourceKindsPlugin: Plugin = {
  config: {
    enums: {
      'resource-node': { kind: { ...RESOURCE_KINDS } },
    },
  },
};
