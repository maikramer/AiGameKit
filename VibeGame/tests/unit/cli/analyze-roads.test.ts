import { describe, expect, it } from 'bun:test';
import type { ParsedElement } from '../../../src/core';
import { checkRoadNetworks } from '../../../src/cli/analyze/roads';

function el(
  tagName: string,
  attributes: Record<string, string | number> = {},
  children: ParsedElement[] = []
): ParsedElement {
  return { tagName, attributes, children };
}

describe('analyze checkRoadNetworks', () => {
  it('flags orphan Ways', () => {
    const root = el('world', {}, [
      el('RoadNetwork', {}, [
        el('Way', { id: 'lonely', xz: '0 0' }),
        el('Way', { id: 'a', xz: '1 0' }),
        el('Way', { id: 'b', xz: '2 0' }),
        el('Segment', { a: 'a', b: 'b' }),
      ]),
    ]);
    const issues = checkRoadNetworks(root);
    expect(issues.some((i) => /orphan Way id="lonely"/.test(i.message))).toBe(
      true
    );
  });

  it('infos plaza ↛ tip across river gap', () => {
    const root = el('world', {}, [
      el('RoadNetwork', {}, [
        el('Way', { id: 'plaza', xz: '0 0' }),
        el('Way', { id: 's_bank', xz: '0 -50' }),
        el('Way', { id: 's_resume', xz: '0 -70' }),
        el('Way', { id: 's_end', xz: '0 -140' }),
        el('Segment', { a: 'plaza', b: 's_bank' }),
        el('Segment', { a: 's_resume', b: 's_end' }),
      ]),
    ]);
    const issues = checkRoadNetworks(root);
    expect(issues.some((i) => i.message.includes('plaza ↛ s_end'))).toBe(true);
  });
});
