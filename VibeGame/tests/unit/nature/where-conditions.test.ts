import { describe, expect, it } from 'bun:test';
import {
  inBand,
  matchesWhere,
  parseRangeBand,
  type SiteFeatures,
} from '../../../src/plugins/nature/rules';

function features(overrides?: Partial<SiteFeatures>): SiteFeatures {
  return {
    altitude: 10,
    slopeDeg: 5,
    biomeId: null,
    biomeType: null,
    waterDist: null,
    roadDist: null,
    onBank: false,
    noise: 0.5,
    ...overrides,
  };
}

describe('parseRangeBand', () => {
  it('parses closed, open and exact bands', () => {
    expect(parseRangeBand('2..16', 'x')).toEqual({ min: 2, max: 16 });
    expect(parseRangeBand('18..', 'x')).toEqual({ min: 18, max: Infinity });
    expect(parseRangeBand('..6', 'x')).toEqual({ min: -Infinity, max: 6 });
    expect(parseRangeBand('5', 'x')).toEqual({ min: 5, max: 5 });
    expect(parseRangeBand('-3..-1', 'x')).toEqual({ min: -3, max: -1 });
  });

  it('returns undefined for absent/empty values', () => {
    expect(parseRangeBand(undefined, 'x')).toBeUndefined();
    expect(parseRangeBand('', 'x')).toBeUndefined();
  });

  it('throws on malformed input and inverted bands', () => {
    expect(() => parseRangeBand('abc', 'x')).toThrow(/\[nature\]/);
    expect(() => parseRangeBand('10..2', 'x')).toThrow(/min > max/);
  });

  it('inBand is inclusive on both ends', () => {
    const band = parseRangeBand('2..16', 'x')!;
    expect(inBand(2, band)).toBe(true);
    expect(inBand(16, band)).toBe(true);
    expect(inBand(1.9, band)).toBe(false);
    expect(inBand(16.1, band)).toBe(false);
  });
});

describe('matchesWhere', () => {
  it('empty condition matches anything', () => {
    expect(matchesWhere({}, features())).toBe(true);
  });

  it('altitude band', () => {
    const where = { altitude: parseRangeBand('1..15', 'a') };
    expect(matchesWhere(where, features({ altitude: 15 }))).toBe(true);
    expect(matchesWhere(where, features({ altitude: 15.1 }))).toBe(false);
  });

  it('slope band', () => {
    const where = { slope: parseRangeBand('14..', 's') };
    expect(matchesWhere(where, features({ slopeDeg: 14 }))).toBe(true);
    expect(matchesWhere(where, features({ slopeDeg: 5 }))).toBe(false);
  });

  it('biome matches region id or type name (case-insensitive)', () => {
    const where = { biome: ['floresta', 'swamp-zone'] };
    expect(matchesWhere(where, features({ biomeType: 'floresta' }))).toBe(true);
    expect(matchesWhere(where, features({ biomeId: 'swamp-zone' }))).toBe(true);
    expect(matchesWhere(where, features({ biomeId: 'desert' }))).toBe(false);
    expect(matchesWhere(where, features())).toBe(false);
  });

  it('water="in" requires a non-positive water distance', () => {
    const where = { waterMode: 'in' as const };
    expect(matchesWhere(where, features({ waterDist: -1 }))).toBe(true);
    expect(matchesWhere(where, features({ waterDist: 0 }))).toBe(true);
    expect(matchesWhere(where, features({ waterDist: 2 }))).toBe(false);
    expect(matchesWhere(where, features({ waterDist: null }))).toBe(false);
  });

  it('water="bank" requires the carved ring flag', () => {
    const where = { waterMode: 'bank' as const };
    expect(matchesWhere(where, features({ onBank: true }))).toBe(true);
    expect(matchesWhere(where, features({ onBank: false }))).toBe(false);
  });

  it('water-dist band fails when the world has no water', () => {
    const where = { waterDist: parseRangeBand('0..12', 'w') };
    expect(matchesWhere(where, features({ waterDist: 5 }))).toBe(true);
    expect(matchesWhere(where, features({ waterDist: null }))).toBe(false);
  });

  it('road-dist band, negative over the carve', () => {
    const where = { roadDist: parseRangeBand('16..', 'r') };
    expect(matchesWhere(where, features({ roadDist: 16 }))).toBe(true);
    expect(matchesWhere(where, features({ roadDist: -2 }))).toBe(false);
    expect(matchesWhere(where, features({ roadDist: null }))).toBe(false);
  });

  it('noise band masks patches', () => {
    const where = { noise: parseRangeBand('0.3..', 'n') };
    expect(matchesWhere(where, features({ noise: 0.31 }))).toBe(true);
    expect(matchesWhere(where, features({ noise: 0.29 }))).toBe(false);
  });

  it('near requires matching host distances (and fails without them)', () => {
    const where = {
      nearSpecies: ['oak'],
      nearDist: parseRangeBand('0..9', 'nd'),
    };
    const withOak = new Map([['oak', 4]]);
    const farOak = new Map([['oak', 20]]);
    const noOak = new Map([['pine', 3]]);
    expect(matchesWhere(where, features(), withOak)).toBe(true);
    expect(matchesWhere(where, features(), farOak)).toBe(false);
    expect(matchesWhere(where, features(), noOak)).toBe(false);
    // Without the planner's distance map (scatter phase) near always fails.
    expect(matchesWhere(where, features())).toBe(false);
  });

  it('conditions AND together', () => {
    const where = {
      altitude: parseRangeBand('10..', 'a'),
      slope: parseRangeBand('..20', 's'),
      roadDist: parseRangeBand('16..', 'r'),
    };
    const good = features({ altitude: 12, slopeDeg: 10, roadDist: 30 });
    expect(matchesWhere(where, good)).toBe(true);
    expect(
      matchesWhere(where, features({ altitude: 12, slopeDeg: 10, roadDist: 5 }))
    ).toBe(false);
  });
});
