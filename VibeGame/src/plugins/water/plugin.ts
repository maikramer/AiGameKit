import type { Adapter, Parser, Plugin, Recipe } from '../../core';
import { Transform } from '../transforms/components';
import { Lake } from './components';
import { LakeApplySystem, WaterAnimSystem } from './systems';

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

const colorAdapter: Adapter = (entity, value) => {
  let hex = String(value).trim();
  if (hex.startsWith('#')) hex = hex.slice(1);
  if (hex.startsWith('0x') || hex.startsWith('0X')) hex = hex.slice(2);
  const n = parseInt(hex, 16);
  if (!Number.isNaN(n)) Lake.color[entity] = n >>> 0;
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

export const WaterPlugin: Plugin = {
  systems: [LakeApplySystem, WaterAnimSystem],
  recipes: [lakeRecipe],
  components: {
    lake: Lake,
  },
  config: {
    defaults: {
      lake: {
        radius: 6,
        depth: 1.5,
        waterOffset: 0.3,
        color: 0x2f7a9a,
        opacity: 0.78,
        ripple: 1,
        waterY: 0,
        applied: 0,
      },
    },
    adapters: {
      lake: {
        color: colorAdapter,
      },
    },
    parsers: {
      Lake: lakeParser,
    },
  },
};
