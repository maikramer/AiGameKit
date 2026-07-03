import type { Adapter, Parser, Plugin, Recipe } from '../../core';
import { Transform } from '../transforms/components';
import { Lake, River, setRiverPath } from './components';
import { LakeApplySystem, RiverApplySystem, WaterAnimSystem } from './systems';
import { WaterInteractionSystem, WaterRippleFxSystem } from './effects';

/**
 * `<Lake at="70 46" radius="6" depth="1.5" color="#2f7a9a">` — sculpted
 * water body. `at` is the world XZ centre; the bowl is carved into the
 * terrain at the local rim elevation (this is NOT a global water plane).
 */
export const lakeRecipe: Recipe = {
  name: 'Lake',
  components: ['transform', 'lake'],
  parserAttributes: ['at'],
};

/**
 * `<River path="0 0 100 20 200 15" width="8" depth="2">` — sculpted river
 * channel. `path` is a space-separated flat list of numbers `[x0,z0,x1,z1,...]`
 * in world coords (the XML parser returns it as a number[] via its vector rule).
 */
export const riverRecipe: Recipe = {
  name: 'River',
  components: ['transform', 'river'],
  parserAttributes: [
    'path',
    'width',
    'depth',
    'color',
    'opacity',
    'ripple',
    'wave-height',
    'wave-speed',
  ],
};

const colorAdapter: Adapter = (entity, value) => {
  let hex = String(value).trim();
  if (hex.startsWith('#')) hex = hex.slice(1);
  if (hex.startsWith('0x') || hex.startsWith('0X')) hex = hex.slice(2);
  const n = parseInt(hex, 16);
  if (!Number.isNaN(n)) Lake.color[entity] = n >>> 0;
};

const riverColorAdapter: Adapter = (entity, value) => {
  // The XML parser may have already converted "#3a5a7a" / "0x3a5a7a" to a
  // number; accept that directly. Otherwise parse the hex string.
  if (typeof value === 'number') {
    River.color[entity] = value >>> 0;
    return;
  }
  let hex = String(value).trim();
  if (hex.startsWith('#')) hex = hex.slice(1);
  if (hex.startsWith('0x') || hex.startsWith('0X')) hex = hex.slice(2);
  const n = parseInt(hex, 16);
  if (!Number.isNaN(n)) River.color[entity] = n >>> 0;
};

const lakeParser: Parser = ({ entity, element }) => {
  const at = element.attributes.at;
  if (at == null) return;
  const v = at as { x?: number; y?: number } | string;
  let x = 0;
  let z = 0;
  if (typeof v === 'string') {
    const parts = v.trim().split(/\s+/).map(Number);
    x = parts[0] ?? 0;
    z = parts[1] ?? 0;
  } else if (typeof v === 'object') {
    x = Number(v.x) || 0;
    z = Number(v.y) || 0;
  }
  Transform.posX[entity] = x;
  Transform.posZ[entity] = z;
  Transform.dirty[entity] = 1;
};

const riverParser: Parser = ({ state, entity, element }) => {
  const attrs = element.attributes;
  // The XML parser returns `path="0 0 100 20 ..."` as number[] (vector rule).
  // Accept arrays directly; fall back to comma-form strings for hand-editing.
  const raw = attrs.path;
  let path: number[] = [];
  if (Array.isArray(raw)) {
    path = raw.map(Number).filter((n) => Number.isFinite(n));
  } else if (typeof raw === 'string') {
    const points = raw.trim().split(/\s+/);
    for (const pt of points) {
      const [xs, zs] = pt.split(',');
      const x = Number(xs);
      const z = Number(zs);
      if (Number.isFinite(x) && Number.isFinite(z)) path.push(x, z);
    }
  }
  if (path.length >= 4) {
    setRiverPath(state, entity, path);
    // Source = first path point, for consistency with the transform model.
    Transform.posX[entity] = path[0]!;
    Transform.posZ[entity] = path[1]!;
    Transform.dirty[entity] = 1;
  }
  // Color: the XML parser may hand us a number (already parsed from #hex) or a
  // string. Accept both so the adapter double-conversion can't corrupt it.
  const colorRaw = attrs.color;
  if (typeof colorRaw === 'number') {
    River.color[entity] = colorRaw >>> 0;
  } else if (typeof colorRaw === 'string') {
    let hex = colorRaw.trim();
    if (hex.startsWith('#')) hex = hex.slice(1);
    if (hex.startsWith('0x') || hex.startsWith('0X')) hex = hex.slice(2);
    const n = parseInt(hex, 16);
    if (!Number.isNaN(n)) River.color[entity] = n >>> 0;
  }
};

export const WaterPlugin: Plugin = {
  systems: [
    LakeApplySystem,
    RiverApplySystem,
    WaterAnimSystem,
    WaterInteractionSystem,
    WaterRippleFxSystem,
  ],
  recipes: [lakeRecipe, riverRecipe],
  components: {
    lake: Lake,
    river: River,
  },
  config: {
    defaults: {
      lake: {
        radius: 6,
        depth: 1.5,
        waterOffset: 0.3,
        color: 0x2f7a9a,
        opacity: 0.78,
        ripple: 0.6,
        // 0 = auto (scales with radius); override via wave-height="0.05".
        waveHeight: 0,
        waveSpeed: 1,
        waterY: 0,
        applied: 0,
      },
      river: {
        width: 6,
        depth: 1.5,
        waterOffset: 0.3,
        color: 0x3a5a7a,
        opacity: 0.85,
        ripple: 0.6,
        waveHeight: 0.04,
        waveSpeed: 1,
        waterY: 0,
        applied: 0,
      },
    },
    adapters: {
      lake: { color: colorAdapter },
      river: { color: riverColorAdapter },
    },
    parsers: {
      Lake: lakeParser,
      River: riverParser,
    },
  },
};
