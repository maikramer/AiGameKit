import { describe, expect, it } from 'bun:test';
import type { ParsedElement } from '../../../src/core';
import { checkRoadGeometry } from '../../../src/cli/analyze/road-geometry';

function el(
  tagName: string,
  attributes: Record<string, string | number> = {},
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

/** 20m straight road from (0,0) to (20,0), optionally through a lake at 10,0. */
const straightPath = '0 0  20 0';

describe('analyze checkRoadGeometry — water crossings', () => {
  it('warns when a road runs through a lake without a bridge', () => {
    const root = el('world', {}, [
      el('Lake', { at: '10 0', radius: '6', depth: '1.5' }),
      el('Road', { path: straightPath }),
    ]);
    const issues = checkRoadGeometry(root);
    const hit = issues.find((i) =>
      /inside Lake@\(10,0\) water/.test(i.message)
    );
    expect(hit).toBeDefined();
    expect(hit!.severity).toBe('warn');
    expect(hit!.message).toMatch(/runs \d+\.\d+m inside/);
  });

  it('warns when a road follows a river channel without a bridge', () => {
    const root = el('world', {}, [
      el('River', { path: '-10 0  30 0', width: '6', depth: '1.5' }),
      el('Road', { path: straightPath }),
    ]);
    const issues = checkRoadGeometry(root);
    expect(
      issues.some((i) => /inside River@\(-10,0\) water/.test(i.message))
    ).toBe(true);
  });

  it('does not flag bridge roads crossing water', () => {
    const root = el('world', {}, [
      el('Lake', { at: '10 0', radius: '6', depth: '1.5' }),
      el('Road', {
        path: straightPath,
        'bridge-url': '/assets/bridge.glb',
      }),
    ]);
    const issues = checkRoadGeometry(root);
    expect(issues.filter((i) => /water surface/.test(i.message))).toEqual([]);
  });

  it('keeps quiet on roads away from water and near-miss edges', () => {
    const root = el('world', {}, [
      el('Lake', { at: '10 30', radius: '6', depth: '1.5' }),
      el('Road', { path: straightPath }),
    ]);
    expect(checkRoadGeometry(root)).toEqual([]);
  });

  it('infos a bridge that crosses no water body', () => {
    const root = el('world', {}, [
      el('Road', {
        path: straightPath,
        'bridge-url': '/assets/bridge.glb',
      }),
    ]);
    const issues = checkRoadGeometry(root);
    const hit = issues.find((i) =>
      /bridge .* crosses no water/.test(i.message)
    );
    expect(hit).toBeDefined();
    expect(hit!.severity).toBe('info');
  });

  it('flags network segments but exempts bridge profiles', () => {
    const root = el('world', {}, [
      el('Lake', { at: '10 0', radius: '8', depth: '1.5' }),
      el('RoadNetwork', {}, [
        el('Way', { id: 'a', xz: '0 0' }),
        el('Way', { id: 'b', xz: '20 0' }),
        el('Segment', { a: 'a', b: 'b' }),
      ]),
      el('RoadNetwork', {}, [
        el('Way', { id: 'c', xz: '-40 0' }),
        el('Way', { id: 'd', xz: '40 0' }),
        el('Segment', {
          a: 'c',
          b: 'd',
          profile: 'bridge',
          'bridge-url': '/assets/bridge.glb',
        }),
      ]),
    ]);
    const issues = checkRoadGeometry(root);
    const cross = issues.filter((i) => /water surface/.test(i.message));
    expect(cross).toHaveLength(1);
    expect(cross[0]!.message).toContain('a→b');
  });
});

describe('analyze checkRoadGeometry — grades', () => {
  it('warns when authored heights exceed flatten-max-grade', () => {
    // 10m segments rising 4m each → 40% ≫ 22%.
    const root = el('world', {}, [
      el('Road', {
        path: '0 0  10 0  20 0',
        heights: '0 4 8',
      }),
    ]);
    const issues = checkRoadGeometry(root);
    const hit = issues.find((i) => /exceeds flatten-max-grade/.test(i.message));
    expect(hit).toBeDefined();
    expect(hit!.severity).toBe('warn');
    expect(hit!.message).toMatch(/grade 40%/);
  });

  it('passes grades within the limit and honours flatten-max-grade=0', () => {
    const gentle = el('world', {}, [
      el('Road', { path: '0 0  10 0  20 0', heights: '0 1 2' }),
    ]);
    expect(checkRoadGeometry(gentle)).toEqual([]);

    const steep = el('world', {}, [
      el('Road', {
        path: '0 0  10 0',
        heights: '0 4',
        'flatten-max-grade': '0',
      }),
    ]);
    expect(checkRoadGeometry(steep)).toEqual([]);
  });

  it('errors on heights/widths/banks count mismatch', () => {
    const root = el('world', {}, [
      el('Road', { path: '0 0  10 0  20 0', heights: '0 1' }),
      el('Road', { path: '0 0  10 0', widths: '2 2 2' }),
    ]);
    const issues = checkRoadGeometry(root);
    const errors = issues.filter((i) => /values but path has/.test(i.message));
    expect(errors).toHaveLength(2);
    expect(errors.every((i) => i.severity === 'error')).toBe(true);
  });
});

describe('analyze checkRoadGeometry — shape', () => {
  it('warns on hairpin reversal', () => {
    const root = el('world', {}, [el('Road', { path: '0 0  10 0  5 2' })]);
    const issues = checkRoadGeometry(root);
    expect(issues.some((i) => /hairpin at point 1/.test(i.message))).toBe(true);
  });

  it('warns on near-zero-length segments', () => {
    const root = el('world', {}, [
      el('Road', { path: '0 0  10 0  10.001 0  20 0' }),
    ]);
    const issues = checkRoadGeometry(root);
    expect(issues.some((i) => /near-zero-length segment/.test(i.message))).toBe(
      true
    );
  });

  it('keeps quiet on gentle curves', () => {
    const root = el('world', {}, [el('Road', { path: '0 0  10 0  20 4' })]);
    expect(checkRoadGeometry(root)).toEqual([]);
  });
});
