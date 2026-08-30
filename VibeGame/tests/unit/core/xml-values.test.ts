import { describe, expect, it } from 'bun:test';
import {
  flattenNumberList,
  parseBoolAttr,
  parseColorValue,
  parseNumberAttr,
  parseVec3Attr,
  splitNumbers,
  splitTokens,
} from 'aigamekit-vibegame';

describe('parseNumberAttr', () => {
  it('returns the fallback for missing / null / empty values', () => {
    expect(parseNumberAttr(undefined, 5)).toBe(5);
    expect(parseNumberAttr(null, 5)).toBe(5);
    expect(parseNumberAttr('', 5)).toBe(5);
  });

  it('passes numbers through and rejects NaN', () => {
    expect(parseNumberAttr(3.5, 5)).toBe(3.5);
    expect(parseNumberAttr(Number.NaN, 5)).toBe(5);
  });

  it('maps booleans to 1/0', () => {
    expect(parseNumberAttr(true, 5)).toBe(1);
    expect(parseNumberAttr(false, 5)).toBe(0);
  });

  it('parses numeric strings and falls back on garbage', () => {
    expect(parseNumberAttr('12.5', 5)).toBe(12.5);
    expect(parseNumberAttr('  7 ', 5)).toBe(7);
    expect(parseNumberAttr('abc', 5)).toBe(5);
    expect(parseNumberAttr('12abc', 5)).toBe(12);
  });

  it('honors explicit zero instead of falling back (tweening "0" bug)', () => {
    expect(parseNumberAttr('0', 1)).toBe(0);
    expect(parseNumberAttr(0, 1)).toBe(0);
  });
});

describe('parseBoolAttr', () => {
  it('returns the fallback for missing values', () => {
    expect(parseBoolAttr(undefined, true)).toBe(true);
    expect(parseBoolAttr(null, false)).toBe(false);
    expect(parseBoolAttr('', true)).toBe(true);
  });

  it('accepts true/false, 1/0, yes/no, on/off (case-insensitive)', () => {
    expect(parseBoolAttr('true', false)).toBe(true);
    expect(parseBoolAttr('TRUE', false)).toBe(true);
    expect(parseBoolAttr('false', true)).toBe(false);
    expect(parseBoolAttr('1', false)).toBe(true);
    expect(parseBoolAttr('0', true)).toBe(false);
    expect(parseBoolAttr('yes', false)).toBe(true);
    expect(parseBoolAttr('no', true)).toBe(false);
    expect(parseBoolAttr('on', false)).toBe(true);
    expect(parseBoolAttr('off', true)).toBe(false);
  });

  it('honors explicit false instead of falling back (sky "false" bug)', () => {
    expect(parseBoolAttr('false', true)).toBe(false);
    expect(parseBoolAttr('no', true)).toBe(false);
  });

  it('falls back for unrecognized strings and numbers', () => {
    expect(parseBoolAttr('maybe', true)).toBe(true);
    // Numbers are truthy-vs-zero (matching the pre-unification road/spawner
    // behavior), not boolean literals.
    expect(parseBoolAttr(2, false)).toBe(true);
    expect(parseBoolAttr(1, false)).toBe(true);
    expect(parseBoolAttr(0, true)).toBe(false);
  });
});

describe('parseVec3Attr', () => {
  const FALLBACK: [number, number, number] = [1, 2, 3];

  it('returns the fallback for missing values', () => {
    expect(parseVec3Attr(undefined, FALLBACK)).toEqual(FALLBACK);
    expect(parseVec3Attr(null, FALLBACK)).toEqual(FALLBACK);
  });

  it('parses "x y z" strings', () => {
    expect(parseVec3Attr('1 2 3', FALLBACK)).toEqual([1, 2, 3]);
    expect(parseVec3Attr(' 1.5  -2 0.25 ', FALLBACK)).toEqual([1.5, -2, 0.25]);
  });

  it('broadcasts scalars and falls back on short/partial strings', () => {
    expect(parseVec3Attr('5', FALLBACK)).toEqual([5, 5, 5]);
    expect(parseVec3Attr('1 2', FALLBACK)).toEqual(FALLBACK);
    expect(parseVec3Attr('1 x 3', FALLBACK)).toEqual(FALLBACK);
  });

  it('broadcasts numbers', () => {
    expect(parseVec3Attr(4, FALLBACK)).toEqual([4, 4, 4]);
    expect(parseVec3Attr(Number.NaN, FALLBACK)).toEqual(FALLBACK);
  });

  it('parses arrays (len >= 3)', () => {
    expect(parseVec3Attr([1, 2, 3], FALLBACK)).toEqual([1, 2, 3]);
    expect(parseVec3Attr([1, 2], FALLBACK)).toEqual(FALLBACK);
  });

  it('parses {x,y,z} objects, falling back to w for z', () => {
    expect(parseVec3Attr({ x: 1, y: 2, z: 3 }, FALLBACK)).toEqual([1, 2, 3]);
    expect(parseVec3Attr({ x: 1, y: 2, w: 4 }, FALLBACK)).toEqual([1, 2, 4]);
    expect(parseVec3Attr({ x: 1 }, FALLBACK)).toEqual(FALLBACK);
  });
});

describe('parseColorValue', () => {
  it('round-trips XMLValueParser-converted numbers (decimal digit strings)', () => {
    // XMLValueParser turns "#ff6600" into 0xff6600; the adapter layer
    // stringifies it back to "16737792" — must recover 0xff6600, not
    // hex-parse the decimal digits.
    expect(parseColorValue('16737792')).toBe(0xff6600);
    expect(parseColorValue('8947848')).toBe(0x888888);
  });

  it('parses bare hex strings (containing letters)', () => {
    expect(parseColorValue('ff6600')).toBe(0xff6600);
    expect(parseColorValue('a1b2c3')).toBe(0xa1b2c3);
  });

  it('treats all-digit bare strings as converted round-trips', () => {
    // "888888" is ambiguous (hex 0x888888 vs decimal 8947848). The canonical
    // #hex form round-trips to a pure decimal string, so digits win.
    expect(parseColorValue('888888')).toBe(888888);
  });

  it('parses #- and 0x-prefixed hex strings', () => {
    expect(parseColorValue('#ff0000')).toBe(0xff0000);
    expect(parseColorValue('0xFF0000')).toBe(0xff0000);
    expect(parseColorValue('#abc')).toBe(0xabc);
  });

  it('returns NaN for garbage', () => {
    expect(Number.isNaN(parseColorValue('not-a-color'))).toBe(true);
    expect(Number.isNaN(parseColorValue(''))).toBe(true);
  });
});

describe('splitTokens', () => {
  it('splits whitespace-separated tokens and drops empties', () => {
    expect(splitTokens('a b c')).toEqual(['a', 'b', 'c']);
    expect(splitTokens('  a   b\tc\n')).toEqual(['a', 'b', 'c']);
    expect(splitTokens('')).toEqual([]);
    expect(splitTokens('   ')).toEqual([]);
  });

  it('returns [] for non-strings', () => {
    expect(splitTokens(undefined)).toEqual([]);
    expect(splitTokens(null)).toEqual([]);
    expect(splitTokens(42)).toEqual([]);
  });
});

describe('splitNumbers', () => {
  it('splits numeric lists and drops garbage tokens', () => {
    expect(splitNumbers('1 2 3')).toEqual([1, 2, 3]);
    expect(splitNumbers('1.5 -2 0.25')).toEqual([1.5, -2, 0.25]);
    expect(splitNumbers('1 x 3')).toEqual([1, 3]);
    expect(splitNumbers('')).toEqual([]);
  });

  it('returns [] for non-strings', () => {
    expect(splitNumbers(undefined)).toEqual([]);
    expect(splitNumbers({ x: 1, y: 2 })).toEqual([]);
  });
});

describe('flattenNumberList', () => {
  it('flattens strings, arrays, and {x,y}/{x,y,z}/{x,y,z,w} objects', () => {
    expect(flattenNumberList('1 2 3 4')).toEqual([1, 2, 3, 4]);
    expect(flattenNumberList([1, 2, 3])).toEqual([1, 2, 3]);
    expect(flattenNumberList({ x: 1, y: 2 })).toEqual([1, 2]);
    expect(flattenNumberList({ x: 1, y: 2, z: 3 })).toEqual([1, 2, 3]);
    expect(flattenNumberList({ x: 1, y: 2, z: 3, w: 4 })).toEqual([1, 2, 3, 4]);
  });

  it('returns [] for partial objects, garbage, and missing values', () => {
    expect(flattenNumberList({ x: 1 })).toEqual([]);
    expect(flattenNumberList({ x: 'a', y: 2 })).toEqual([]);
    expect(flattenNumberList('not numbers')).toEqual([]);
    expect(flattenNumberList(undefined)).toEqual([]);
    expect(flattenNumberList(null)).toEqual([]);
  });
});
