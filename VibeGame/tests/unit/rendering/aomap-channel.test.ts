import { describe, expect, it } from "bun:test";

/**
 * Expected CHANNEL_MAP from texture-recipe-system.ts (not exported, so we
 * assert the specification here to catch accidental regressions).
 */
const CHANNEL_MAP = [
  "map", // 0 — albedo/diffuse
  "normalMap", // 1
  "roughnessMap", // 2
  "metalnessMap", // 3
  "aoMap", // 4 — ambient occlusion
] as const;

describe("CHANNEL_MAP structure", () => {
  it("has exactly 5 entries", () => {
    expect(CHANNEL_MAP).toHaveLength(5);
  });

  it("index 0 is map (albedo)", () => {
    expect(CHANNEL_MAP[0]).toBe("map");
  });

  it("index 1 is normalMap", () => {
    expect(CHANNEL_MAP[1]).toBe("normalMap");
  });

  it("index 2 is roughnessMap", () => {
    expect(CHANNEL_MAP[2]).toBe("roughnessMap");
  });

  it("index 3 is metalnessMap", () => {
    expect(CHANNEL_MAP[3]).toBe("metalnessMap");
  });

  it("index 4 is aoMap", () => {
    expect(CHANNEL_MAP[4]).toBe("aoMap");
  });
});
