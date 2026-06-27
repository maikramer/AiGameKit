import { describe, expect, it } from 'bun:test';
import {
  aabbContains,
  parsePolygonString,
  pointInPolygon,
} from '../../../../src/plugins/biomes/adapters';

describe('parsePolygonString', () => {
  it('parses a 4-vertex square into vertices and AABB', () => {
    const g = parsePolygonString('-10 -10, 10 -10, 10 10, -10 10');
    expect(g.vertices).toEqual([
      [-10, -10],
      [10, -10],
      [10, 10],
      [-10, 10],
    ]);
    expect(g.minX).toBe(-10);
    expect(g.minZ).toBe(-10);
    expect(g.maxX).toBe(10);
    expect(g.maxZ).toBe(10);
  });

  it('parses a 6-vertex L-shape', () => {
    const g = parsePolygonString('0 0, 20 0, 20 10, 10 10, 10 20, 0 20');
    expect(g.vertices).toHaveLength(6);
    expect(g.minX).toBe(0);
    expect(g.minZ).toBe(0);
    expect(g.maxX).toBe(20);
    expect(g.maxZ).toBe(20);
  });

  it('tolerates extra whitespace and returns degenerate AABB for empty input', () => {
    const g = parsePolygonString('   1   2  ,  3 4 ');
    expect(g.vertices).toEqual([
      [1, 2],
      [3, 4],
    ]);
    const empty = parsePolygonString('');
    expect(empty.vertices).toEqual([]);
    expect(empty.minX).toBe(0);
    expect(empty.maxX).toBe(0);
  });

  it('parses bracket format [x,z;x,z;...] (preferred — prevents engine number auto-conversion)', () => {
    const g = parsePolygonString('[-300,80;300,80;300,400;-300,400]');
    expect(g.vertices).toEqual([
      [-300, 80],
      [300, 80],
      [300, 400],
      [-300, 400],
    ]);
    expect(g.minX).toBe(-300);
    expect(g.minZ).toBe(80);
    expect(g.maxX).toBe(300);
    expect(g.maxZ).toBe(400);
  });

  it('bracket and legacy formats produce identical results', () => {
    const bracket = parsePolygonString('[100,-200;400,-200;400,200;100,200]');
    const legacy = parsePolygonString('100 -200, 400 -200, 400 200, 100 200');
    expect(bracket.vertices).toEqual(legacy.vertices);
    expect(bracket.minX).toBe(legacy.minX);
    expect(bracket.maxZ).toBe(legacy.maxZ);
  });
});

describe('aabbContains', () => {
  const minX = -10;
  const minZ = -10;
  const maxX = 10;
  const maxZ = 10;

  it('returns true for an interior point', () => {
    expect(aabbContains(minX, minZ, maxX, maxZ, 0, 0)).toBe(true);
  });

  it('returns true for a point on the boundary', () => {
    expect(aabbContains(minX, minZ, maxX, maxZ, minX, minZ)).toBe(true);
    expect(aabbContains(minX, minZ, maxX, maxZ, maxX, maxZ)).toBe(true);
  });

  it('returns false for a point outside the box', () => {
    expect(aabbContains(minX, minZ, maxX, maxZ, 50, 0)).toBe(false);
    expect(aabbContains(minX, minZ, maxX, maxZ, 0, -999)).toBe(false);
  });
});

describe('pointInPolygon', () => {
  const square = [
    [-10, -10],
    [10, -10],
    [10, 10],
    [-10, 10],
  ];

  it('returns true at the centre of a square', () => {
    expect(pointInPolygon(0, 0, square)).toBe(true);
  });

  it('returns false well outside a square', () => {
    expect(pointInPolygon(50, 50, square)).toBe(false);
  });

  it('returns false inside the AABB but in the L-shape concavity', () => {
    const lShape = [
      [0, 0],
      [20, 0],
      [20, 10],
      [10, 10],
      [10, 20],
      [0, 20],
    ];
    expect(pointInPolygon(15, 15, lShape)).toBe(false);
    expect(pointInPolygon(5, 5, lShape)).toBe(true);
  });

  it('never matches a polygon with fewer than 3 vertices', () => {
    expect(
      pointInPolygon(0, 0, [
        [0, 0],
        [1, 1],
      ])
    ).toBe(false);
    expect(pointInPolygon(0, 0, [])).toBe(false);
  });
});
