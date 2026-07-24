import type { ParsedElement, XMLValue } from '../../core';
import {
  attrNumber,
  attrString,
  cellToWorld,
  parseCell,
  parseCellRect,
  parseGateSides,
} from './grid';
import { prefabChildren, wallSegmentChildren } from './prefabs';

export type GridCtx = {
  cell: number;
  originX: number;
  originZ: number;
  align: string;
};

function el(
  tagName: string,
  attributes: Record<string, XMLValue>,
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

function placeAt(wx: number, wz: number, align: string): string {
  return `at: ${wx} ${wz}; align-to-terrain: ${align}`;
}

function compositionAt(
  wx: number,
  wz: number,
  align: string,
  children: ParsedElement[],
  opts: { name?: string | null; rot?: number; collider?: string } = {}
): ParsedElement {
  const attrs: Record<string, XMLValue> = {
    place: placeAt(wx, wz, align),
    body: 'fixed',
  };
  if (opts.name) attrs.name = opts.name;
  if (opts.rot && opts.rot !== 0) attrs.euler = `0 ${opts.rot} 0`;
  if (opts.collider) attrs.collider = opts.collider;
  return el('Composition', attrs, children);
}

function buildingOrGltf(
  wx: number,
  wz: number,
  align: string,
  child: ParsedElement,
  defaultPrefab: string
): ParsedElement {
  const name = attrString(child.attributes.name);
  const rot = attrNumber(child.attributes.rot, 0);
  const url = attrString(child.attributes.url);
  const place = placeAt(wx, wz, align);
  const scale = attrString(child.attributes.scale);

  if (url) {
    const goAttrs: Record<string, XMLValue> = { place };
    if (name) goAttrs.name = name;
    if (rot !== 0) goAttrs.euler = `0 ${rot} 0`;
    if (scale) goAttrs.scale = scale;
    return el('GameObject', goAttrs, [el('GLTFLoader', { url })]);
  }

  const prefab = attrString(child.attributes.prefab) ?? defaultPrefab;
  return compositionAt(wx, wz, align, prefabChildren(prefab), { name, rot });
}

export function expandStreet(child: ParsedElement, g: GridCtx): ParsedElement {
  const [fx, fz] = parseCell(child.attributes.from, 'Street from=');
  const [tx, tz] = parseCell(child.attributes.to, 'Street to=');
  const widthCells = attrNumber(child.attributes.width, 1);
  const [x0, z0] = cellToWorld(fx, fz, g.cell, g.originX, g.originZ);
  const [x1, z1] = cellToWorld(tx, tz, g.cell, g.originX, g.originZ);
  const width = Math.max(0.5, widthCells * g.cell * 0.85);
  const attrs: Record<string, XMLValue> = {
    path: `${x0} ${z0} ${x1} ${z1}`,
    width,
  };
  const tex = attrString(child.attributes['texture-url']);
  if (tex) attrs['texture-url'] = tex;
  return el('Road', attrs);
}

/** Closed rectangle of streets on the border of a cell rect. */
export function expandStreetRing(
  child: ParsedElement,
  g: GridCtx
): ParsedElement[] {
  const r = parseCellRect(child.attributes, 'StreetRing');
  const width = attrNumber(child.attributes.width, 1);
  const tex = attrString(child.attributes['texture-url']);
  const mk = (from: string, to: string) => {
    const attrs: Record<string, XMLValue> = { from, to, width };
    if (tex) attrs['texture-url'] = tex;
    return expandStreet(el('Street', attrs), g);
  };
  return [
    mk(`${r.minX} ${r.minZ}`, `${r.maxX} ${r.minZ}`),
    mk(`${r.maxX} ${r.minZ}`, `${r.maxX} ${r.maxZ}`),
    mk(`${r.maxX} ${r.maxZ}`, `${r.minX} ${r.maxZ}`),
    mk(`${r.minX} ${r.maxZ}`, `${r.minX} ${r.minZ}`),
  ];
}

/** Cardinal cross through a cell rect (horizontal + vertical through mid). */
export function expandStreetCross(
  child: ParsedElement,
  g: GridCtx
): ParsedElement[] {
  const r = parseCellRect(child.attributes, 'StreetCross');
  const width = attrNumber(child.attributes.width, 1);
  const midX = (r.minX + r.maxX) / 2;
  const midZ = (r.minZ + r.maxZ) / 2;
  const tex = attrString(child.attributes['texture-url']);
  const mk = (from: string, to: string) => {
    const attrs: Record<string, XMLValue> = { from, to, width };
    if (tex) attrs['texture-url'] = tex;
    return expandStreet(el('Street', attrs), g);
  };
  return [
    mk(`${r.minX} ${midZ}`, `${r.maxX} ${midZ}`),
    mk(`${midX} ${r.minZ}`, `${midX} ${r.maxZ}`),
  ];
}

export function expandBuilding(
  child: ParsedElement,
  g: GridCtx
): ParsedElement {
  const [cx, cz] = parseCell(child.attributes.at, 'Building');
  const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
  return buildingOrGltf(wx, wz, g.align, child, 'house');
}

export function expandProp(child: ParsedElement, g: GridCtx): ParsedElement {
  const [cx, cz] = parseCell(child.attributes.at, 'Prop');
  const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
  const out = buildingOrGltf(wx, wz, g.align, child, 'crate');
  // Props often have no collider (campfire/torch) — allow override
  const collider = attrString(child.attributes.collider);
  if (collider && out.tagName === 'Composition') {
    return el('Composition', { ...out.attributes, collider }, out.children);
  }
  return out;
}

export function expandSlot(child: ParsedElement, g: GridCtx): ParsedElement {
  const [cx, cz] = parseCell(child.attributes.at, 'Slot');
  const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
  const name = attrString(child.attributes.name);
  const role = attrString(child.attributes.role);
  const attrs: Record<string, XMLValue> = {
    place: placeAt(wx, wz, g.align),
  };
  if (name) attrs.name = name;
  else if (role) attrs.name = `slot.${role}`;
  return el('GameObject', attrs);
}

function wallLookFrom(child: ParsedElement) {
  return {
    color: attrString(child.attributes.color) ?? '#6a6a66',
    textureUrl: attrString(child.attributes['texture-url']),
    normalMapUrl: attrString(child.attributes['normal-map-url']),
    textureRepeat: attrString(child.attributes['texture-repeat']),
  };
}

export function expandPlaza(child: ParsedElement, g: GridCtx): ParsedElement {
  const r = parseCellRect(child.attributes, 'Plaza');
  const cx = (r.minX + r.maxX) / 2;
  const cz = (r.minZ + r.maxZ) / 2;
  const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
  const w = Math.max(g.cell, (r.maxX - r.minX + 1) * g.cell);
  const d = Math.max(g.cell, (r.maxZ - r.minZ + 1) * g.cell);
  const color = attrString(child.attributes.color) ?? '#6b4a2b';
  const name = attrString(child.attributes.name);
  const feather = attrNumber(child.attributes['edge-feather'], 1.2);
  const padAttrs: Record<string, XMLValue> = {
    pos: '0 0 0',
    size: `${w} ${d}`,
    color,
    'edge-feather': feather,
  };
  const tex = attrString(child.attributes['texture-url']);
  if (tex) {
    padAttrs['texture-url'] = tex;
    padAttrs['texture-repeat'] =
      attrString(child.attributes['texture-repeat']) ?? '4 4';
  }
  return compositionAt(wx, wz, g.align, [el('Pad', padAttrs)], {
    name,
    collider: 'none',
  });
}

function wallAlong(
  x0: number,
  z0: number,
  x1: number,
  z1: number,
  g: GridCtx,
  height: number,
  thickness: number,
  look: ReturnType<typeof wallLookFrom>,
  name: string | null
): ParsedElement {
  const dx = x1 - x0;
  const dz = z1 - z0;
  const len = Math.hypot(dx, dz) || g.cell;
  const midX = (x0 + x1) / 2;
  const midZ = (z0 + z1) / 2;
  const yaw = (Math.atan2(dx, dz) * 180) / Math.PI;
  return compositionAt(
    midX,
    midZ,
    g.align,
    wallSegmentChildren(len, height, thickness, look),
    { name, rot: yaw }
  );
}

export function expandWall(child: ParsedElement, g: GridCtx): ParsedElement {
  const [fx, fz] = parseCell(child.attributes.from, 'Wall from=');
  const [tx, tz] = parseCell(child.attributes.to, 'Wall to=');
  const [x0, z0] = cellToWorld(fx, fz, g.cell, g.originX, g.originZ);
  const [x1, z1] = cellToWorld(tx, tz, g.cell, g.originX, g.originZ);
  const height = attrNumber(child.attributes.height, 3.5);
  const thickness = attrNumber(child.attributes.thickness, 0.7);
  const name = attrString(child.attributes.name);
  return wallAlong(
    x0,
    z0,
    x1,
    z1,
    g,
    height,
    thickness,
    wallLookFrom(child),
    name
  );
}

/**
 * Rectangular curtain wall. `gates="n,e,s,w"` opens a gap (no wall) on that side.
 */
export function expandWallRect(
  child: ParsedElement,
  g: GridCtx
): ParsedElement[] {
  const r = parseCellRect(child.attributes, 'WallRect');
  const height = attrNumber(child.attributes.height, 3.5);
  const thickness = attrNumber(child.attributes.thickness, 0.7);
  const look = wallLookFrom(child);
  const gates = parseGateSides(child.attributes.gates);
  const [x0, z0] = cellToWorld(r.minX, r.minZ, g.cell, g.originX, g.originZ);
  const [x1, z1] = cellToWorld(r.maxX, r.maxZ, g.cell, g.originX, g.originZ);
  const out: ParsedElement[] = [];
  const side = (
    key: string,
    ax: number,
    az: number,
    bx: number,
    bz: number,
    label: string
  ) => {
    if (gates.has(key)) return;
    out.push(wallAlong(ax, az, bx, bz, g, height, thickness, look, label));
  };
  side('s', x0, z0, x1, z0, 'wall.s');
  side('e', x1, z0, x1, z1, 'wall.e');
  side('n', x1, z1, x0, z1, 'wall.n');
  side('w', x0, z1, x0, z0, 'wall.w');

  // Optional gate arches in openings
  if (attrString(child.attributes['gate-prefab']) || gates.size > 0) {
    const gatePrefab = attrString(child.attributes['gate-prefab']) ?? 'gate';
    const mid = (a: number, b: number) => (a + b) / 2;
    const placeGate = (key: string, wx: number, wz: number, rot: number) => {
      if (!gates.has(key)) return;
      out.push(
        compositionAt(wx, wz, g.align, prefabChildren(gatePrefab), {
          name: `gate.${key}`,
          rot,
        })
      );
    };
    placeGate('s', mid(x0, x1), z0, 0);
    placeGate('n', mid(x0, x1), z1, 180);
    placeGate('e', x1, mid(z0, z1), 90);
    placeGate('w', x0, mid(z0, z1), -90);
  }
  return out;
}

/** Row of buildings from `from`→`to` stepping by `step` cells (default 1). */
export function expandBuildingRow(
  child: ParsedElement,
  g: GridCtx
): ParsedElement[] {
  const [fx, fz] = parseCell(child.attributes.from, 'BuildingRow from=');
  const [tx, tz] = parseCell(child.attributes.to, 'BuildingRow to=');
  const step = Math.max(1, Math.round(attrNumber(child.attributes.step, 1)));
  const prefab = attrString(child.attributes.prefab) ?? 'house';
  const url = attrString(child.attributes.url);
  const baseRot = attrNumber(child.attributes.rot, 0);
  const namePrefix = attrString(child.attributes.name) ?? 'row';
  const dx = Math.sign(tx - fx) || 0;
  const dz = Math.sign(tz - fz) || 0;
  if (dx === 0 && dz === 0) {
    throw new Error('[BuildingRow] from and to must differ');
  }
  if (dx !== 0 && dz !== 0) {
    throw new Error('[BuildingRow] must be axis-aligned (same x or same z)');
  }
  const out: ParsedElement[] = [];
  const span = dx !== 0 ? Math.abs(tx - fx) : Math.abs(tz - fz);
  const count = Math.floor(span / step) + 1;
  for (let i = 0; i < count && i < 256; i++) {
    const cx = fx + dx * step * i;
    const cz = fz + dz * step * i;
    const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
    const attrs: Record<string, XMLValue> = {
      prefab,
      rot: baseRot,
      name: `${namePrefix}.${i}`,
    };
    if (url) attrs.url = url;
    out.push(buildingOrGltf(wx, wz, g.align, el('Building', attrs), prefab));
  }
  return out;
}

/**
 * Fill or ring a cell rect with buildings.
 * `mode="perimeter"` (default) | `"fill"`.
 */
export function expandBlock(child: ParsedElement, g: GridCtx): ParsedElement[] {
  const r = parseCellRect(child.attributes, 'Block');
  const mode = (attrString(child.attributes.mode) ?? 'perimeter').toLowerCase();
  const prefab = attrString(child.attributes.prefab) ?? 'house';
  const step = Math.max(1, Math.round(attrNumber(child.attributes.step, 1)));
  const namePrefix = attrString(child.attributes.name) ?? 'block';
  const cells: Array<[number, number]> = [];
  for (let x = r.minX; x <= r.maxX; x += step) {
    for (let z = r.minZ; z <= r.maxZ; z += step) {
      const onEdge =
        x === r.minX || x === r.maxX || z === r.minZ || z === r.maxZ;
      if (mode === 'fill' || onEdge) cells.push([x, z]);
    }
  }
  return cells.map(([cx, cz], i) => {
    const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
    const fake = el('Building', {
      prefab,
      rot: attrNumber(child.attributes.rot, 0),
      name: `${namePrefix}.${i}`,
      ...(attrString(child.attributes.url)
        ? { url: attrString(child.attributes.url)! }
        : {}),
    });
    return buildingOrGltf(wx, wz, g.align, fake, prefab);
  });
}

export function expandGate(child: ParsedElement, g: GridCtx): ParsedElement {
  const [cx, cz] = parseCell(child.attributes.at, 'Gate');
  const [wx, wz] = cellToWorld(cx, cz, g.cell, g.originX, g.originZ);
  const facing = (attrString(child.attributes.facing) ?? 'n').toLowerCase();
  const rotMap: Record<string, number> = {
    n: 180,
    north: 180,
    s: 0,
    south: 0,
    e: -90,
    east: -90,
    w: 90,
    west: 90,
  };
  const rot = attrNumber(child.attributes.rot, rotMap[facing] ?? 0);
  const attrs: Record<string, XMLValue> = {
    prefab: attrString(child.attributes.prefab) ?? 'gate',
    rot,
    name: attrString(child.attributes.name) ?? `gate.${facing}`,
  };
  const url = attrString(child.attributes.url);
  if (url) attrs.url = url;
  return buildingOrGltf(wx, wz, g.align, el('Building', attrs), 'gate');
}
