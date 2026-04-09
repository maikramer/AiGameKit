import { describe, expect, it } from 'bun:test';
import { SRGBColorSpace, LinearSRGBColorSpace } from 'three';

function resolveColorSpace(channel: number): string {
  return channel === 0 ? SRGBColorSpace : LinearSRGBColorSpace;
}

describe('colorSpace assignment per channel', () => {
  it('channel 0 (albedo/map) → SRGBColorSpace', () => {
    expect(resolveColorSpace(0)).toBe(SRGBColorSpace);
  });

  it('channel 1 (normalMap) → LinearSRGBColorSpace', () => {
    expect(resolveColorSpace(1)).toBe(LinearSRGBColorSpace);
  });

  it('channel 2 (roughnessMap) → LinearSRGBColorSpace', () => {
    expect(resolveColorSpace(2)).toBe(LinearSRGBColorSpace);
  });

  it('channel 3 (metalnessMap) → LinearSRGBColorSpace', () => {
    expect(resolveColorSpace(3)).toBe(LinearSRGBColorSpace);
  });

  it('channel 4 (aoMap) → LinearSRGBColorSpace', () => {
    expect(resolveColorSpace(4)).toBe(LinearSRGBColorSpace);
  });
});
