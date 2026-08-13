import { afterEach, describe, expect, it } from 'bun:test';
import type { ParsedElement } from '../../../src/core';
import {
  applyWorldXmlHooks,
  clearWorldXmlHooks,
  onWorldXml,
} from '../../../src/core';

function tree(): ParsedElement {
  return {
    tagName: 'Scene',
    attributes: {},
    children: [
      {
        tagName: 'RaceTrack',
        attributes: { width: 12 },
        children: [],
      },
      {
        tagName: 'Group',
        attributes: {},
        children: [
          { tagName: 'AiVehicle', attributes: {}, children: [] },
          { tagName: 'PlayerVehicle', attributes: {}, children: [] },
        ],
      },
    ],
  };
}

afterEach(() => clearWorldXmlHooks());

describe('world XML hooks', () => {
  it('runs hooks in registration order', () => {
    const order: string[] = [];
    onWorldXml(() => order.push('first'));
    onWorldXml(() => order.push('second'));
    applyWorldXmlHooks(tree());
    expect(order).toEqual(['first', 'second']);
  });

  it('lets a hook write generated attributes into an included tag', () => {
    const root = tree();
    onWorldXml((r) => {
      const track = r.children.find((c) => c.tagName === 'RaceTrack');
      if (track) track.attributes.centerline = '0 0 0 10 0 0';
    });
    applyWorldXmlHooks(root);
    expect(root.children[0]!.attributes.centerline).toBe('0 0 0 10 0 0');
  });

  it('lets a hook prune entities out of the world', () => {
    const root = tree();
    onWorldXml((r) => {
      for (const child of r.children) {
        child.children = child.children.filter(
          (c) => c.tagName !== 'AiVehicle'
        );
      }
    });
    applyWorldXmlHooks(root);
    expect(root.children[1]!.children.map((c) => c.tagName)).toEqual([
      'PlayerVehicle',
    ]);
  });

  it('isolates a throwing hook and keeps going', () => {
    const errors: unknown[] = [];
    const seen: string[] = [];
    onWorldXml(() => {
      throw new Error('boom');
    });
    onWorldXml(() => seen.push('ran'));
    applyWorldXmlHooks(tree(), (e) => errors.push(e));
    expect(seen).toEqual(['ran']);
    expect((errors[0] as Error).message).toBe('boom');
  });

  it('unregisters through the returned disposer', () => {
    const seen: string[] = [];
    const off = onWorldXml(() => seen.push('ran'));
    off();
    applyWorldXmlHooks(tree());
    expect(seen).toEqual([]);
  });

  it('is a no-op with no hooks registered', () => {
    const root = tree();
    expect(() => applyWorldXmlHooks(root)).not.toThrow();
  });
});
