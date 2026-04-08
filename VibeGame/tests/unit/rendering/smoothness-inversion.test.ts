import { describe, expect, it } from "bun:test";

/**
 * Mirrors the inversion logic from texture-recipe-system.ts:
 *   roughness = 255 - smoothness  (R channel)
 */
function invertSmoothness(value: number): number {
  return 255 - value;
}

/**
 * Mirrors the auto-detection condition:
 *   channel === 2 && url.toLowerCase().includes("smoothness")
 */
function shouldInvert(channel: number, url: string): boolean {
  return channel === 2 && url.toLowerCase().includes("smoothness");
}

describe("smoothness → roughness pixel inversion", () => {
  it("204 → 51 (smoothness 0.8 → roughness 0.2)", () => {
    expect(invertSmoothness(204)).toBe(51);
  });

  it("0 → 255 (smoothness 0.0 → roughness 1.0)", () => {
    expect(invertSmoothness(0)).toBe(255);
  });

  it("255 → 0 (smoothness 1.0 → roughness 0.0)", () => {
    expect(invertSmoothness(255)).toBe(0);
  });

  it("128 → 127 (midpoint)", () => {
    expect(invertSmoothness(128)).toBe(127);
  });
});

describe("smoothness auto-detection", () => {
  it("detects 'smoothness' in URL on channel 2", () => {
    expect(shouldInvert(2, "wood_smoothness.png")).toBe(true);
  });

  it("case-insensitive match", () => {
    expect(shouldInvert(2, "Wood_SMOOTHNESS.PNG")).toBe(true);
  });

  it("no match when channel is not roughness (2)", () => {
    expect(shouldInvert(0, "wood_smoothness.png")).toBe(false);
    expect(shouldInvert(1, "wood_smoothness.png")).toBe(false);
    expect(shouldInvert(3, "wood_smoothness.png")).toBe(false);
    expect(shouldInvert(4, "wood_smoothness.png")).toBe(false);
  });

  it("no match when URL lacks 'smoothness'", () => {
    expect(shouldInvert(2, "wood_roughness.png")).toBe(false);
    expect(shouldInvert(2, "generic_map.png")).toBe(false);
  });
});
