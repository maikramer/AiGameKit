import type { ParticleSystemParameters } from 'three.quarks';
import { RenderMode } from 'three.quarks';
import { ConstantValue, IntervalValue } from 'quarks.core';
import { ColorRange } from 'quarks.core';
import { SphereEmitter, ConeEmitter, RectangleEmitter } from 'quarks.core';
import { SizeOverLife, ColorOverLife, ApplyForce } from 'quarks.core';
import { PiecewiseBezier, Bezier, Gradient } from 'quarks.core';
import { Vector3, Vector4 } from 'quarks.core';
import { particleMaterial } from './textures';

export type PresetName =
  | 'fire'
  | 'rain'
  | 'snow'
  | 'smoke'
  | 'dust'
  | 'explosion'
  | 'sparks'
  | 'magic'
  | 'fireflies'
  | 'splash'
  | 'woodchips'
  | 'rockshards'
  | 'leaves'
  | 'ground-dust';

// New presets append at the end: ParticleEmitter.preset stores the index.
const PRESET_NAMES: readonly PresetName[] = [
  'fire',
  'rain',
  'snow',
  'smoke',
  'dust',
  'explosion',
  'sparks',
  'magic',
  'fireflies',
  'splash',
  'woodchips',
  'rockshards',
  'leaves',
  'ground-dust',
];

/** Aliases used by games that don't match PRESET_NAMES exactly. */
const PRESET_ALIASES: Record<string, PresetName> = {
  sparkle: 'magic',
  sparkles: 'magic',
  'sand-dust': 'ground-dust',
  sanddust: 'ground-dust',
};

/**
 * `preset` values the XML parser accepts → `ParticleEmitter.preset` index.
 *
 * Derived from PRESET_NAMES *and* PRESET_ALIASES on purpose: the plugin used
 * to hand-copy only the canonical names into `config.enums`, so an alias that
 * `presetIndex` resolved happily (`preset="sparkle"`) was rejected by the
 * attribute validator — and a rejected enum aborts the whole world parse, not
 * just that one emitter.
 */
export function presetEnumValues(): Record<string, number> {
  const out: Record<string, number> = {};
  PRESET_NAMES.forEach((name, index) => {
    out[name] = index;
  });
  for (const [alias, target] of Object.entries(PRESET_ALIASES)) {
    out[alias] = PRESET_NAMES.indexOf(target);
  }
  return out;
}

/**
 * ConeEmitter shoots along local +Z. These presets need emitter.rotation.x =
 * -PI/2 so the cone points world +Y (up).
 */
const UPRIGHT_CONE_PRESETS: ReadonlySet<PresetName> = new Set([
  'fire',
  'smoke',
  'sparks',
  'splash',
]);

export function presetNeedsUprightCone(name: PresetName): boolean {
  return UPRIGHT_CONE_PRESETS.has(name);
}

export function presetIndex(name: string): number {
  const resolved = PRESET_ALIASES[name] ?? name;
  const idx = PRESET_NAMES.indexOf(resolved as PresetName);
  return idx >= 0 ? idx : 0;
}

export function presetName(index: number): PresetName {
  return PRESET_NAMES[index] ?? 'fire';
}

/** Constant downward / upward world acceleration (quarks ApplyForce). */
function worldForce(y: number, magnitude: number): ApplyForce {
  return new ApplyForce(new Vector3(0, y, 0), new ConstantValue(magnitude));
}

function firePreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'fire', additive: true }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.5, 1.4),
    startSpeed: new IntervalValue(1.5, 3.5),
    startSize: new IntervalValue(0.35, 0.7),
    startColor: new ColorRange(
      new Vector4(1, 0.85, 0.25, 1),
      new Vector4(1, 0.35, 0.05, 1)
    ),
    emissionOverTime: new ConstantValue(55),
    shape: new ConeEmitter({ radius: 0.12, angle: Math.PI / 7 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.85, 0.45, 0.05), 0]])
      ),
      new ColorOverLife(
        new Gradient(
          [
            [new Vector3(1, 0.9, 0.4), 0],
            [new Vector3(1, 0.45, 0.1), 0.45],
            [new Vector3(0.25, 0.08, 0.02), 1],
          ],
          [
            [1, 0],
            [0.85, 0.4],
            [0, 1],
          ]
        )
      ),
      worldForce(1, 1.8),
    ],
  };
}

function rainPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'rain', opacity: 0.9 }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.4, 0.9),
    startSpeed: new ConstantValue(18),
    startSize: new IntervalValue(0.06, 0.12),
    startColor: new ColorRange(
      new Vector4(0.75, 0.85, 1, 0.7),
      new Vector4(0.9, 0.95, 1, 0.95)
    ),
    emissionOverTime: new ConstantValue(280),
    shape: new SphereEmitter({ radius: 12, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [worldForce(-1, 28)],
  };
}

function snowPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'snow', opacity: 1 }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(3, 6),
    startSpeed: new IntervalValue(0.4, 1.2),
    startSize: new IntervalValue(0.08, 0.2),
    startColor: new ColorRange(
      new Vector4(1, 1, 1, 0.9),
      new Vector4(1, 1, 1, 1)
    ),
    emissionOverTime: new ConstantValue(90),
    shape: new SphereEmitter({ radius: 15, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [worldForce(-1, 1.2)],
  };
}

function smokePreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'smoke', opacity: 1 }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(2.5, 5),
    startSpeed: new IntervalValue(1.2, 2.4),
    startSize: new IntervalValue(0.55, 1.1),
    startColor: new ColorRange(
      new Vector4(0.55, 0.55, 0.55, 0.85),
      new Vector4(0.75, 0.75, 0.75, 0.55)
    ),
    emissionOverTime: new ConstantValue(28),
    // Cone + upright emitter rotation → rises; buoyancy keeps plume climbing.
    shape: new ConeEmitter({ radius: 0.18, angle: Math.PI / 5 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(0.7, 1.2, 1.8, 2.8), 0]])
      ),
      new ColorOverLife(
        new Gradient(
          [
            [new Vector3(0.55, 0.55, 0.55), 0],
            [new Vector3(0.7, 0.7, 0.7), 0.45],
            [new Vector3(0.85, 0.85, 0.85), 1],
          ],
          [
            [0.9, 0],
            [0.55, 0.5],
            [0, 1],
          ]
        )
      ),
      worldForce(1, 2.4),
    ],
  };
}

function dustPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'dust', opacity: 0.95 }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(1.2, 3),
    startSpeed: new IntervalValue(0.4, 1.4),
    startSize: new IntervalValue(0.12, 0.32),
    startColor: new ColorRange(
      new Vector4(0.76, 0.7, 0.5, 0.75),
      new Vector4(0.9, 0.85, 0.7, 0.95)
    ),
    emissionOverTime: new ConstantValue(40),
    shape: new SphereEmitter({ radius: 1.2 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 1.1, 0.9, 0.35), 0]])
      ),
      worldForce(-1, 2.5),
    ],
  };
}

/** Low sheet of sand/dust that skims the ground (ambient desert FX). */
function groundDustPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'dust', opacity: 0.55 }),
    looping: true,
    duration: 8,
    startLife: new IntervalValue(1.6, 3.2),
    startSpeed: new IntervalValue(0.15, 0.55),
    startSize: new IntervalValue(0.18, 0.45),
    startColor: new ColorRange(
      new Vector4(0.82, 0.72, 0.48, 0.35),
      new Vector4(0.92, 0.84, 0.62, 0.55)
    ),
    emissionOverTime: new ConstantValue(28),
    shape: new RectangleEmitter({ width: 8, height: 6, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(0.7, 1, 0.85, 0.2), 0]])
      ),
      new ColorOverLife(
        new Gradient(
          [
            [new Vector3(0.85, 0.75, 0.5), 0],
            [new Vector3(0.78, 0.68, 0.45), 0.55],
            [new Vector3(0.7, 0.6, 0.4), 1],
          ],
          [
            [0.45, 0],
            [0.35, 0.4],
            [0, 1],
          ]
        )
      ),
      // Drift along +X with a faint upward lift (skims terrain).
      new ApplyForce(new Vector3(1, 0.08, 0.25), new ConstantValue(1.1)),
    ],
  };
}

function explosionPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'explosion', additive: true }),
    looping: false,
    duration: 0.7,
    autoDestroy: true,
    startLife: new IntervalValue(0.4, 1.0),
    startSpeed: new IntervalValue(4, 10),
    startSize: new IntervalValue(0.45, 1.0),
    startColor: new ColorRange(
      new Vector4(1, 0.9, 0.35, 1),
      new Vector4(1, 0.4, 0.1, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(80),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new SphereEmitter({ radius: 0.15 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.75, 0.35, 0), 0]])
      ),
      worldForce(-1, 6),
    ],
  };
}

function sparksPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'sparks', additive: true }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.25, 0.7),
    startSpeed: new IntervalValue(6, 14),
    startSize: new IntervalValue(0.08, 0.16),
    startColor: new ColorRange(
      new Vector4(1, 1, 0.55, 1),
      new Vector4(1, 0.75, 0.2, 1)
    ),
    emissionOverTime: new ConstantValue(45),
    shape: new ConeEmitter({ radius: 0.06, angle: Math.PI / 7 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [worldForce(-1, 12)],
  };
}

function magicPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'magic', additive: true }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.9, 1.8),
    startSpeed: new IntervalValue(0.6, 2.2),
    startSize: new IntervalValue(0.18, 0.4),
    startColor: new ColorRange(
      new Vector4(0.55, 0.25, 1, 1),
      new Vector4(0.35, 0.55, 1, 1)
    ),
    emissionOverTime: new ConstantValue(45),
    shape: new SphereEmitter({ radius: 0.55 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(0.6, 1.1, 0.9, 0.1), 0]])
      ),
      new ColorOverLife(
        new Gradient(
          [
            [new Vector3(0.6, 0.3, 1), 0],
            [new Vector3(0.3, 0.5, 1), 0.5],
            [new Vector3(0.1, 0.2, 0.6), 1],
          ],
          [
            [1, 0],
            [0.7, 0.5],
            [0.15, 1],
          ]
        )
      ),
      worldForce(1, 0.8),
    ],
  };
}

function firefliesPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'fireflies', additive: true }),
    looping: true,
    duration: 5,
    startLife: new IntervalValue(2.5, 5),
    startSpeed: new IntervalValue(0.15, 0.55),
    startSize: new IntervalValue(0.08, 0.18),
    startColor: new ColorRange(
      new Vector4(0.65, 1, 0.25, 1),
      new Vector4(0.95, 1, 0.45, 1)
    ),
    emissionOverTime: new ConstantValue(14),
    shape: new SphereEmitter({ radius: 3.5 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(0.4, 1.1, 0.7, 0.35), 0]])
      ),
    ],
  };
}

function splashPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'splash', opacity: 1 }),
    looping: false,
    duration: 0.8,
    autoDestroy: true,
    startLife: new IntervalValue(0.35, 0.75),
    startSpeed: new IntervalValue(2.5, 6.5),
    startSize: new IntervalValue(0.1, 0.28),
    startColor: new ColorRange(
      new Vector4(0.85, 0.95, 1, 1),
      new Vector4(0.6, 0.85, 1, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(55),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new ConeEmitter({ radius: 0.3, angle: Math.PI / 3.2 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [
      worldForce(-1, 18),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.9, 0.55, 0.1), 0]])
      ),
    ],
  };
}

/** Splinters that fly off a tree with each axe blow. */
function woodchipsPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'woodchips' }),
    looping: false,
    duration: 1.0,
    autoDestroy: true,
    startLife: new IntervalValue(0.4, 0.95),
    startSpeed: new IntervalValue(3, 7.5),
    startSize: new IntervalValue(0.1, 0.28),
    startColor: new ColorRange(
      new Vector4(0.55, 0.36, 0.18, 1),
      new Vector4(0.8, 0.62, 0.38, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(36),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new ConeEmitter({ radius: 0.2, angle: Math.PI / 2.4 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [
      worldForce(-1, 20),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.95, 0.7, 0.25), 0]])
      ),
    ],
  };
}

/** Angular grey fragments knocked off a rock by the pickaxe. */
function rockshardsPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'rockshards' }),
    looping: false,
    duration: 1.0,
    autoDestroy: true,
    startLife: new IntervalValue(0.35, 0.85),
    startSpeed: new IntervalValue(3.5, 8.5),
    startSize: new IntervalValue(0.12, 0.32),
    startColor: new ColorRange(
      new Vector4(0.45, 0.45, 0.47, 1),
      new Vector4(0.72, 0.72, 0.75, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(32),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new ConeEmitter({ radius: 0.25, angle: Math.PI / 2.2 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      worldForce(-1, 24),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.9, 0.6, 0.2), 0]])
      ),
    ],
  };
}

/** Slow fluttering foliage puff for a felled tree's canopy. */
function leavesPreset(): Partial<SystemParams> {
  return {
    material: particleMaterial({ preset: 'leaves', opacity: 1 }),
    looping: false,
    duration: 2.8,
    autoDestroy: true,
    startLife: new IntervalValue(1.4, 2.8),
    startSpeed: new IntervalValue(0.8, 2.6),
    startSize: new IntervalValue(0.18, 0.4),
    startColor: new ColorRange(
      new Vector4(0.25, 0.5, 0.15, 1),
      new Vector4(0.45, 0.7, 0.25, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(48),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new SphereEmitter({ radius: 1.0, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      worldForce(-1, 1.8),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.95, 0.85, 0.4), 0]])
      ),
    ],
  };
}

type SystemParams = ParticleSystemParameters;

const PRESET_FACTORIES: Record<PresetName, () => Partial<SystemParams>> = {
  fire: firePreset,
  rain: rainPreset,
  snow: snowPreset,
  smoke: smokePreset,
  dust: dustPreset,
  explosion: explosionPreset,
  sparks: sparksPreset,
  magic: magicPreset,
  fireflies: firefliesPreset,
  splash: splashPreset,
  woodchips: woodchipsPreset,
  rockshards: rockshardsPreset,
  leaves: leavesPreset,
  'ground-dust': groundDustPreset,
};

export function createPresetParams(name: PresetName): Partial<SystemParams> {
  const factory = PRESET_FACTORIES[name];
  return factory ? factory() : firePreset();
}

export { PRESET_NAMES };
