import type { ParsedElement } from '../../core';

/** Structural buildings for `<Building prefab=…>`. */
export const BUILDING_PREFABS = [
  'house',
  'house-wide',
  'cottage',
  'market-stall',
  'tower',
  'watchtower',
  'chapel',
  'forge',
  'barn',
  'longhouse',
  'gate',
] as const;

/** Amenity / prop prefabs for `<Prop prefab=…>` (also valid on Building). */
export const PROP_PREFABS = [
  'well',
  'campfire',
  'flagpole',
  'torch',
  'shrine',
  'crate',
  'bench',
  'fountain',
] as const;

export type BuildingPrefabId = (typeof BUILDING_PREFABS)[number];
export type PropPrefabId = (typeof PROP_PREFABS)[number];

function box(pos: string, size: string, color: string): ParsedElement {
  return { tagName: 'Box', attributes: { pos, size, color }, children: [] };
}

function cyl(
  pos: string,
  size: string,
  color: string,
  rotation?: string
): ParsedElement {
  const attributes: Record<string, string> = { pos, size, color };
  if (rotation) attributes.rotation = rotation;
  return { tagName: 'Cylinder', attributes, children: [] };
}

function sphere(pos: string, size: string, color: string): ParsedElement {
  return { tagName: 'Sphere', attributes: { pos, size, color }, children: [] };
}

const CATALOG: Record<string, () => ParsedElement[]> = {
  house: () => [
    box('0 1.2 0', '3.5 2.4 3.5', '#9a8a70'),
    box('0 2.6 0', '3.9 0.2 3.9', '#5a4a3a'),
  ],
  'house-wide': () => [
    box('0 1.2 0', '5.2 2.4 3.6', '#8a7a60'),
    box('0 2.6 0', '5.6 0.22 4.0', '#4a3a2a'),
  ],
  cottage: () => [
    box('0 1.0 0', '2.8 2.0 2.8', '#a09070'),
    box('0 2.2 0', '3.2 0.18 3.2', '#6a4030'),
    box('0 1.1 1.45', '0.9 1.4 0.12', '#5a4030'),
  ],
  'market-stall': () => [
    box('0 0.5 0', '3 1 1.5', '#8a5a2a'),
    box('0 1.4 0', '2.4 0.1 1.0', '#5a3a18'),
    box('0 2.0 -0.6', '3 0.1 2', '#b03030'),
    box('1.4 1.8 -0.6', '0.12 0.6 0.12', '#4a3020'),
    box('-1.4 1.8 -0.6', '0.12 0.6 0.12', '#4a3020'),
  ],
  tower: () => [
    box('0 2.5 0', '2.4 5 2.4', '#7a7a78'),
    box('0 5.2 0', '2.8 0.35 2.8', '#5a5a58'),
  ],
  watchtower: () => [
    box('0 3.0 0', '2.2 6 2.2', '#6a6a68'),
    box('0 6.2 0', '3.0 0.3 3.0', '#4a4a48'),
    box('0 6.6 0', '0.9 0.8 0.9', '#8a8a70'),
  ],
  chapel: () => [
    box('0 1.6 0', '4.5 3.2 5.5', '#c8c0b0'),
    box('0 3.5 0', '5.0 0.25 6.0', '#5a4a3a'),
    box('0 4.4 0', '1.2 1.6 1.2', '#b0a898'),
    box('0 5.4 0', '0.15 0.8 0.15', '#d4b84a'),
  ],
  forge: () => [
    box('0 1.3 0', '4.2 2.6 3.8', '#6a5a50'),
    box('0 2.8 0', '4.6 0.2 4.2', '#3a3028'),
    cyl('1.2 2.2 0.8', '0.7 0.7 1.6', '#4a4038'),
    sphere('1.2 3.1 0.8', '0.35', '#ff6622'),
  ],
  barn: () => [
    box('0 1.8 0', '6.0 3.6 4.2', '#8a6038'),
    box('0 3.9 0', '6.5 0.25 4.6', '#5a3a20'),
    box('0 1.2 2.15', '2.2 2.4 0.15', '#4a3020'),
  ],
  longhouse: () => [
    box('0 1.5 0', '8.0 3.0 3.5', '#7a6a50'),
    box('0 3.2 0', '8.5 0.22 3.9', '#4a3a28'),
  ],
  gate: () => [
    box('-2.2 1.8 0', '0.6 3.6 1.2', '#6a6a60'),
    box('2.2 1.8 0', '0.6 3.6 1.2', '#6a6a60'),
    box('0 3.7 0', '5.0 0.5 1.4', '#5a5a52'),
  ],
  well: () => [
    cyl('0 0.4 0', '1.1 1.1 0.8', '#6a6a6a'),
    box('0 1.4 0', '0.12 1.2 0.12', '#4a3020'),
    box('0 1.9 0', '1.6 0.1 1.6', '#5a4a3a'),
  ],
  campfire: () => [
    cyl('0 0.12 0', '1.0 1.15 0.25', '#6f6a63'),
    cyl('0 0.35 0', '0.12 0.12 1.5', '#5a3a20', '1.5 0 0.2'),
    cyl('0 0.35 0', '0.12 0.12 1.5', '#6b4a2b', '1.5 1.0 -0.2'),
    sphere('0 0.7 0', '0.55', '#ff7722'),
    sphere('0 1.05 0', '0.32', '#ffcc33'),
  ],
  flagpole: () => [
    box('0 2.0 0', '0.1 4.0 0.1', '#5a3a20'),
    sphere('0 4.05 0', '0.13', '#d4b84a'),
    box('0.5 3.2 0', '0.06 1.0 0.8', '#b03b3b'),
  ],
  torch: () => [
    box('0 0.9 0', '0.12 1.8 0.12', '#4a3020'),
    sphere('0 1.9 0', '0.22', '#ff8822'),
  ],
  shrine: () => [
    box('0 0.5 0', '1.4 1.0 1.0', '#8a8a80'),
    box('0 1.2 0', '1.0 0.4 0.7', '#c8c0a8'),
    sphere('0 1.7 0', '0.28', '#d4b84a'),
  ],
  crate: () => [
    box('0 0.4 0', '0.9 0.8 0.9', '#8a5a2a'),
    box('0 0.82 0', '0.95 0.08 0.95', '#6a4020'),
  ],
  bench: () => [
    box('0 0.35 0', '1.4 0.12 0.45', '#6a4a28'),
    box('-0.55 0.18 0', '0.1 0.35 0.4', '#5a3a20'),
    box('0.55 0.18 0', '0.1 0.35 0.4', '#5a3a20'),
  ],
  fountain: () => [
    cyl('0 0.25 0', '2.0 2.0 0.5', '#8a8a90'),
    cyl('0 0.7 0', '0.5 0.5 0.9', '#a0a0a8'),
    sphere('0 1.3 0', '0.35', '#6ab0d0'),
  ],
};

export function listAllPrefabs(): string[] {
  return [...BUILDING_PREFABS, ...PROP_PREFABS];
}

export function prefabChildren(prefab: string): ParsedElement[] {
  const id = prefab.trim().toLowerCase();
  const factory = CATALOG[id];
  if (!factory) {
    throw new Error(
      `[CityLayout] unknown prefab="${prefab}". ` +
        `Known: ${listAllPrefabs().join('|')} or set url= for a GLB.`
    );
  }
  return factory();
}

export type WallLook = {
  color?: string;
  textureUrl?: string | null;
  normalMapUrl?: string | null;
  textureRepeat?: string | null;
};

/** Wall segment boxes centered at local origin, length along +X. */
export function wallSegmentChildren(
  length: number,
  height: number,
  thickness: number,
  look: WallLook | string = '#6a6a66'
): ParsedElement[] {
  const opts: WallLook = typeof look === 'string' ? { color: look } : look;
  const color = opts.color ?? '#6a6a66';
  const h = Math.max(0.5, height);
  const t = Math.max(0.2, thickness);
  const L = Math.max(0.5, length);
  const bodyAttrs: Record<string, string> = {
    pos: `0 ${h / 2} 0`,
    size: `${L} ${h} ${t}`,
    color,
  };
  const capAttrs: Record<string, string> = {
    pos: `0 ${h + 0.15} 0`,
    size: `${L} 0.3 ${t + 0.15}`,
    color: '#5a5a56',
  };
  if (opts.textureUrl) {
    bodyAttrs['texture-url'] = opts.textureUrl;
    capAttrs['texture-url'] = opts.textureUrl;
    const rep = opts.textureRepeat ?? '2 1';
    bodyAttrs['texture-repeat'] = rep;
    capAttrs['texture-repeat'] = rep;
  }
  if (opts.normalMapUrl) {
    bodyAttrs['normal-map-url'] = opts.normalMapUrl;
    capAttrs['normal-map-url'] = opts.normalMapUrl;
  }
  return [
    { tagName: 'Box', attributes: bodyAttrs, children: [] },
    { tagName: 'Box', attributes: capAttrs, children: [] },
  ];
}
