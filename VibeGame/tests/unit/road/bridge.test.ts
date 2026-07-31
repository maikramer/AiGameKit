import { describe, expect, it } from 'bun:test';
import {
  bridgeDeckYAt,
  bridgeLipCost,
  bridgeMidXZ,
  bridgeSpanFitRatio,
  bridgeSpanScaleX,
  bridgeYawDeg,
  chooseBridgeLip,
  deckContourAt,
  deckContourCrown,
  deckContourTipY,
  fillContourGaps,
  pickSolidBankY,
  pathArcFraction,
  pathArcLength,
  pathPointAtArc,
  planDeckOriginY,
  BRIDGE_DECK_LOCAL_Y,
  BRIDGE_MAX_CROWN_ABOVE_LIP,
  BRIDGE_NATIVE_SPAN_M,
  BRIDGE_TIP_EMBED_M,
} from '../../../src/plugins/road/bridge';
import {
  bridgeApproachCorridorOpts,
  carveBridgeDeckClearance,
  effectiveBridgeApproachMeters,
  BRIDGE_DECK_UNDERCUT_M,
} from '../../../src/plugins/road/carve';
import type { HeightSampler } from '../../../src/plugins/terrain/height-sampler';
import { sampleHeightAt } from '../../../src/plugins/terrain/height-sampler';
import {
  stitchEndToEndChains,
  detectRoadJunctions,
} from '../../../src/plugins/road/junctions';
import type { RoadJunctionInput } from '../../../src/plugins/road/junctions';

function coarseSampler(world = 2000, size = 64): HeightSampler {
  return {
    width: size,
    height: size,
    data: new Float32Array(size * size).fill(0.5),
    worldSize: world,
    maxHeight: 100,
  };
}

describe('bridge helpers', () => {
  it('pathArcFraction lerps 0→1 along a straight span', () => {
    const path = [0, 0, 10, 0, 20, 0];
    expect(pathArcFraction(path, 0, 0)).toBeCloseTo(0, 3);
    expect(pathArcFraction(path, 10, 0)).toBeCloseTo(0.5, 3);
    expect(pathArcFraction(path, 20, 0)).toBeCloseTo(1, 3);
  });

  it('bridgeDeckYAt lerps bank heights by arc', () => {
    const path = [0, 0, 40, 0];
    expect(bridgeDeckYAt(10, 20, path, 0, 0)).toBeCloseTo(10);
    expect(bridgeDeckYAt(10, 20, path, 20, 0)).toBeCloseTo(15);
    expect(bridgeDeckYAt(10, 20, path, 40, 0)).toBeCloseTo(20);
  });

  it('bridgeSpanScaleX uses native mesh length', () => {
    const path = [4, -104, 4, -142]; // 38 m
    expect(pathArcLength(path)).toBeCloseTo(38);
    expect(bridgeSpanScaleX(path, 18)).toBeCloseTo(38 / 18);
    expect(bridgeSpanFitRatio(path, 18)).toBeCloseTo(38 / 18);
  });

  it('bridgeYawDeg / midXZ align +X to path', () => {
    const path = [0, 0, 10, 0];
    expect(bridgeYawDeg(path)).toBeCloseTo(0);
    expect(bridgeMidXZ(path)).toEqual({ x: 5, z: 0 });
    const ns = [0, 0, 0, -10];
    expect(bridgeYawDeg(ns)).toBeCloseTo(90);
  });

  it('effectiveBridgeApproachMeters grows on coarse heightmaps', () => {
    const fine = coarseSampler(200, 512);
    const coarse = coarseSampler(2000, 64);
    // Fine: authored 6 m is enough.
    expect(effectiveBridgeApproachMeters(fine, 6)).toBe(6);
    // Coarse ~31.7 m/texel → approach ≥ 2.5 texels.
    const eff = effectiveBridgeApproachMeters(coarse, 6);
    expect(eff).toBeGreaterThan(6);
    expect(eff).toBeGreaterThanOrEqual((2000 / 63) * 2.5 - 0.01);
  });

  it('bridgeApproachCorridorOpts softens grade/sink vs artery defaults', () => {
    const s = coarseSampler(200, 128);
    const opts = bridgeApproachCorridorOpts(s, {
      path: [0, 0, 40, 0],
      width: 4,
      falloff: 2,
      window: 56,
      maxGrade: 0.22,
      approachMeters: 6,
    });
    expect(opts.maxGrade!).toBeLessThanOrEqual(0.12);
    expect(opts.platformSink!).toBeLessThan(0.12);
    expect(opts.window).toBeLessThan(56);
    expect(opts.falloff).toBeGreaterThanOrEqual(2);
  });

  it('bridgeApproachCorridorOpts clamps artery-wide falloff (no river plug)', () => {
    const s = coarseSampler(200, 512);
    const opts = bridgeApproachCorridorOpts(s, {
      path: [0, 0, 18, 0],
      width: 2,
      falloff: 16,
      window: 112,
      maxGrade: 0.22,
      approachMeters: 8,
    });
    // Cap at approach*0.35 (=2.8) — not the 16 m artery shoulder.
    expect(opts.falloff).toBeLessThanOrEqual(3.5);
  });

  it('native span constant matches bridge mesh contract', () => {
    expect(BRIDGE_NATIVE_SPAN_M).toBe(18);
    // Pre-load estimate: crown of the shipped LODs (wood≈1.95 / stone≈2.18).
    expect(BRIDGE_DECK_LOCAL_Y).toBeGreaterThan(1.5);
    expect(BRIDGE_DECK_LOCAL_Y).toBeLessThan(3);
  });

  it('pathPointAtArc walks and clamps the polyline', () => {
    const path = [0, 0, 10, 0, 10, 10];
    expect(pathPointAtArc(path, 0)).toEqual({ x: 0, z: 0 });
    expect(pathPointAtArc(path, 5)).toEqual({ x: 5, z: 0 });
    expect(pathPointAtArc(path, 15)).toEqual({ x: 10, z: 5 });
    expect(pathPointAtArc(path, 99)).toEqual({ x: 10, z: 10 });
  });

  it('pickSolidBankY takes the lowest solid sample (ignore flatten spikes)', () => {
    expect(pickSolidBankY([34.9, 40.0, 34.8], 31)).toBeCloseTo(34.8);
    expect(pickSolidBankY([30.0, 30.2], 31)).toBeCloseTo(30.2);
  });
});

describe('bridge lip grading decision', () => {
  it('bridgeLipCost prices cut and fill separately', () => {
    // Seating on the low bank is pure cut; on the high bank pure fill.
    const low = bridgeLipCost(33, 33, 36, 28);
    expect(low.cut).toBeCloseTo(3);
    expect(low.fill).toBe(0);
    const high = bridgeLipCost(36, 33, 36, 28);
    expect(high.fill).toBeCloseTo(3);
    expect(high.cut).toBe(0);
    // Same volume of work, but fill is dearer.
    expect(high.cost).toBeGreaterThan(low.cost);
  });

  it('bridgeLipCost surcharges fill on a bank that barely clears the water', () => {
    const deep = bridgeLipCost(34.5, 34, 35, 28);
    const shallow = bridgeLipCost(34.5, 34, 35, 33.7);
    expect(shallow.fill).toBeCloseTo(deep.fill);
    expect(shallow.cost).toBeGreaterThan(deep.cost * 2);
  });

  it('chooseBridgeLip uses mean when banks already level', () => {
    const plan = chooseBridgeLip(34.9, 34.8, 31);
    expect(plan.strategy).toBe('match-mean');
    expect(plan.lip).toBeCloseTo(34.85, 1);
    expect(plan.cut).toBeLessThan(0.1);
    expect(plan.fill).toBeLessThan(0.1);
  });

  it('chooseBridgeLip splits the difference between two solid banks', () => {
    // Deep channel: both banks can take grading, so share cut and fill.
    const plan = chooseBridgeLip(36, 33, 28);
    expect(plan.strategy).toBe('match-mean');
    expect(plan.lip).toBeCloseTo(34.5);
    expect(plan.cut).toBeCloseTo(1.5);
    expect(plan.fill).toBeCloseTo(1.5);
  });

  it('chooseBridgeLip cuts instead of filling when the low bank is shallow', () => {
    // Low bank only 0.8 m above the channel — fill would dam the river.
    const plan = chooseBridgeLip(34.2, 33.6, 32.8);
    expect(plan.strategy).toBe('match-low');
    expect(plan.lip).toBeCloseTo(33.6);
    expect(plan.fill).toBe(0);
  });

  it('chooseBridgeLip keeps the deck above water when both samples are in the cut', () => {
    const plan = chooseBridgeLip(28.2, 28.4, 28);
    expect(plan.lip).toBeGreaterThan(28.4);
  });
});

describe('bridge deck contour', () => {
  /** ramp → plateau → ramp, like the shipped stone/wood LODs. */
  const contour = [0.3, 0.9, 1.5, 2.0, 2.0, 2.0, 1.5, 0.9, 0.3];

  it('fillContourGaps back-fills probe misses from both ends', () => {
    expect(fillContourGaps([null, 1, null, 3, null])).toEqual([1, 1, 1, 3, 3]);
    expect(fillContourGaps([null, null])).toBeNull();
  });

  it('deckContourAt interpolates and clamps by arc fraction', () => {
    expect(deckContourAt(contour, 0)).toBeCloseTo(0.3);
    expect(deckContourAt(contour, 0.5)).toBeCloseTo(2.0);
    expect(deckContourAt(contour, 1)).toBeCloseTo(0.3);
    expect(deckContourAt(contour, 0.0625)).toBeCloseTo(0.6);
    expect(deckContourAt(contour, 2)).toBeCloseTo(0.3);
  });

  it('tip and crown read the abutments and the plateau', () => {
    expect(deckContourTipY(contour)).toBeCloseTo(0.3);
    expect(deckContourCrown(contour)).toBeCloseTo(2.0);
  });

  it('planDeckOriginY sinks only the tips and lifts the plateau clear', () => {
    const lip = 34.93;
    const originY = planDeckOriginY(contour, lip);
    const tip = originY + deckContourTipY(contour);
    const crown = originY + deckContourCrown(contour);
    expect(tip).toBeCloseTo(lip - BRIDGE_TIP_EMBED_M);
    // The plateau clears the bank instead of being dragged down to it.
    expect(crown).toBeGreaterThan(lip + 1);
  });

  it('planDeckOriginY clamps a mesh far taller than the span', () => {
    const tall = [0, 10, 0];
    const lip = 30;
    const originY = planDeckOriginY(tall, lip);
    const crown = originY + deckContourCrown(tall);
    expect(crown).toBeCloseTo(lip + BRIDGE_MAX_CROWN_ABOVE_LIP);
  });

  it('planDeckOriginY never buries the crown under the lip', () => {
    // Flat deck (no ramps): tips are the crown, so embedding them would hide
    // the whole surface — the crown stays on the lip instead.
    const flat = [1, 1, 1];
    const originY = planDeckOriginY(flat, 20);
    expect(originY + deckContourCrown(flat)).toBeCloseTo(20);
  });
});

describe('bridge deck clearance carve', () => {
  function flatSampler(height: number): HeightSampler {
    const size = 65;
    return {
      width: size,
      height: size,
      data: new Float32Array(size * size).fill(height / 100),
      worldSize: 64,
      maxHeight: 100,
    };
  }

  it('cuts terrain that pokes through the deck and leaves the rest alone', () => {
    const sampler = flatSampler(35);
    const path = [-20, 0, 20, 0];
    // Deck arcs from 34.8 at the tips up to 40 at the crown.
    const deckYAt = (u: number) => 34.8 + Math.sin(u * Math.PI) * 5.2;
    const changed = carveBridgeDeckClearance(sampler, {
      path,
      width: 6,
      falloff: 2,
      deckYAt,
    });
    expect(changed).toBe(true);
    // Near the tip the deck is under the ground → terrain cut to clear it.
    expect(sampleHeightAt(sampler, -20, 0)).toBeCloseTo(
      34.8 - BRIDGE_DECK_UNDERCUT_M,
      1
    );
    // Mid-span the deck is metres above → untouched.
    expect(sampleHeightAt(sampler, 0, 0)).toBeCloseTo(35, 3);
    // Outside the corridor → untouched.
    expect(sampleHeightAt(sampler, 0, 20)).toBeCloseTo(35, 3);
  });

  it('never raises terrain (a channel under the span stays open)', () => {
    const sampler = flatSampler(20);
    const changed = carveBridgeDeckClearance(sampler, {
      path: [-20, 0, 20, 0],
      width: 6,
      falloff: 2,
      deckYAt: () => 35,
    });
    expect(changed).toBe(false);
    expect(sampleHeightAt(sampler, 0, 0)).toBeCloseTo(20, 3);
  });
});

describe('bridge stitch exclusion', () => {
  it('does not stitch bridge into arterial end-to-end chain', () => {
    const roads: RoadJunctionInput[] = [
      {
        eid: 1,
        path: [0, 0, 0, -20],
        width: 2,
        edgeFeather: 0.7,
        textureUrl: '/cobble.png',
        normalMapUrl: null,
        textureScale: 16,
      },
      {
        eid: 2,
        path: [0, -20, 0, -40],
        width: 2,
        edgeFeather: 0.7,
        textureUrl: '/cobble.png',
        normalMapUrl: null,
        textureScale: 16,
        bridge: true,
      },
      {
        eid: 3,
        path: [0, -40, 0, -60],
        width: 2,
        edgeFeather: 0.7,
        textureUrl: '/cobble.png',
        normalMapUrl: null,
        textureScale: 16,
      },
    ];
    const junctions = detectRoadJunctions(roads);
    const chains = stitchEndToEndChains(roads, junctions);
    // Bridge breaks the chain — no single 3-member stitch across the river.
    for (const c of chains) {
      expect(c.memberEids.includes(2)).toBe(false);
      expect(c.memberEids.length).toBeLessThan(3);
    }
  });
});
