import { describe, expect, it } from 'bun:test';
import {
  expandIncludes,
  unwrapIncludeFragment,
} from '../../../src/core/xml/include';

describe('unwrapIncludeFragment', () => {
  it('strips Scene wrapper', () => {
    expect(unwrapIncludeFragment('<Scene>\n  <Group></Group>\n</Scene>')).toBe(
      '<Group></Group>'
    );
  });

  it('strips xml declaration and world wrapper', () => {
    expect(
      unwrapIncludeFragment('<?xml version="1.0"?>\n<world><Box></Box></world>')
    ).toBe('<Box></Box>');
  });

  it('returns bare fragment as-is', () => {
    expect(unwrapIncludeFragment('<Group name="a"></Group>')).toBe(
      '<Group name="a"></Group>'
    );
  });
});

describe('expandIncludes', () => {
  it('inlines a single include', async () => {
    const files: Record<string, string> = {
      '/world/a.xml': '<Group name="a"></Group>',
    };
    const out = await expandIncludes('<Include src="/world/a.xml"></Include>', {
      load: async (src) => files[src]!,
    });
    expect(out).toContain('<Group name="a"></Group>');
    expect(out).not.toMatch(/include/i);
  });

  it('expands nested includes', async () => {
    const files: Record<string, string> = {
      '/world/outer.xml':
        '<Group name="outer"><Include src="/world/inner.xml"></Include></Group>',
      '/world/inner.xml': '<GameObject name="inner"></GameObject>',
    };
    const out = await expandIncludes(
      '<Include src="/world/outer.xml"></Include>',
      { load: async (src) => files[src]! }
    );
    expect(out).toContain('name="outer"');
    expect(out).toContain('name="inner"');
  });

  it('fails on cycles', async () => {
    const files: Record<string, string> = {
      '/world/a.xml': '<Include src="/world/b.xml"></Include>',
      '/world/b.xml': '<Include src="/world/a.xml"></Include>',
    };
    await expect(
      expandIncludes('<Include src="/world/a.xml"></Include>', {
        load: async (src) => files[src]!,
      })
    ).rejects.toThrow(/cycle/);
  });

  it('fails on missing src', async () => {
    await expect(
      expandIncludes('<Include></Include>', {
        load: async () => '',
      })
    ).rejects.toThrow(/missing src/);
  });

  it('fails when load rejects', async () => {
    await expect(
      expandIncludes('<Include src="/missing.xml"></Include>', {
        load: async () => {
          throw new Error('ENOENT');
        },
      })
    ).rejects.toThrow(/failed to load/);
  });

  it('accepts self-closing include in XML fragments', async () => {
    const out = await expandIncludes('<Include src="/x.xml" />', {
      load: async () => '<Pad></Pad>',
    });
    expect(out.trim()).toBe('<Pad></Pad>');
  });

  it('respects maxDepth', async () => {
    const files: Record<string, string> = {
      '/0.xml': '<Include src="/1.xml"></Include>',
      '/1.xml': '<Include src="/2.xml"></Include>',
      '/2.xml': '<ok></ok>',
    };
    await expect(
      expandIncludes('<Include src="/0.xml"></Include>', {
        load: async (src) => files[src]!,
        maxDepth: 1,
      })
    ).rejects.toThrow(/max depth/);
  });

  it('ignores Include mentions inside HTML comments', async () => {
    const out = await expandIncludes(
      '<!-- docs: <Include src="/fake.xml"> -->\n<Group name="ok"></Group>',
      {
        load: async () => {
          throw new Error('should not load');
        },
      }
    );
    expect(out).toContain('name="ok"');
    expect(out).toContain('<!-- docs:');
  });
});
