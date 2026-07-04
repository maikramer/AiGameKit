import * as THREE from 'three';
import type { ParticleSystemParameters } from 'three.quarks';
import { RenderMode } from 'three.quarks';
import { ConstantValue, IntervalValue } from 'quarks.core';
import { ColorRange } from 'quarks.core';
import { SphereEmitter, ConeEmitter } from 'quarks.core';
import { SizeOverLife, ColorOverLife, GravityForce } from 'quarks.core';
import { PiecewiseBezier, Bezier, Gradient } from 'quarks.core';
import { Vector3, Vector4 } from 'quarks.core';

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
  | 'leaves';

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
];

export function presetIndex(name: string): number {
  const idx = PRESET_NAMES.indexOf(name as PresetName);
  return idx >= 0 ? idx : 0;
}

export function presetName(index: number): PresetName {
  return PRESET_NAMES[index] ?? 'fire';
}

function firePreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.4, 1.2),
    startSpeed: new IntervalValue(1, 3),
    startSize: new ConstantValue(0.3),
    startColor: new ColorRange(
      new Vector4(1, 0.8, 0.2, 1),
      new Vector4(1, 0.3, 0.05, 1)
    ),
    emissionOverTime: new ConstantValue(40),
    shape: new ConeEmitter({ radius: 0.1, angle: Math.PI / 6 }),
    worldSpace: false,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(new PiecewiseBezier([[new Bezier(1, 0.6, 0.3, 0), 0]])),
      new ColorOverLife(
        new Gradient(
          [
            [new Vector3(1, 0.9, 0.4), 0],
            [new Vector3(1, 0.4, 0.1), 0.5],
            [new Vector3(0.3, 0.1, 0.02), 1],
          ],
          [
            [1, 0],
            [0, 1],
          ]
        )
      ),
    ],
  };
}

function rainPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.6,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.3, 0.8),
    startSpeed: new ConstantValue(20),
    startSize: new ConstantValue(0.03),
    startColor: new ColorRange(
      new Vector4(0.7, 0.8, 1, 0.4),
      new Vector4(0.9, 0.95, 1, 0.6)
    ),
    emissionOverTime: new ConstantValue(200),
    shape: new SphereEmitter({ radius: 10, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [new GravityForce(new Vector3(0, 0, 0), -30)],
  };
}

function snowPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.9,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(2, 5),
    startSpeed: new IntervalValue(0.3, 1),
    startSize: new IntervalValue(0.04, 0.12),
    startColor: new ColorRange(
      new Vector4(1, 1, 1, 0.8),
      new Vector4(1, 1, 1, 1)
    ),
    emissionOverTime: new ConstantValue(60),
    shape: new SphereEmitter({ radius: 15, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
  };
}

function smokePreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.5,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(2, 4),
    startSpeed: new IntervalValue(0.5, 1.5),
    startSize: new ConstantValue(0.2),
    startColor: new ColorRange(
      new Vector4(0.5, 0.5, 0.5, 0.6),
      new Vector4(0.8, 0.8, 0.8, 0.3)
    ),
    emissionOverTime: new ConstantValue(20),
    shape: new SphereEmitter({ radius: 0.2 }),
    worldSpace: false,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(0.2, 0.8, 1.5, 2.5), 0]])
      ),
    ],
  };
}

function dustPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.4,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(1, 3),
    startSpeed: new IntervalValue(0.1, 0.5),
    startSize: new IntervalValue(0.03, 0.08),
    startColor: new ColorRange(
      new Vector4(0.76, 0.7, 0.5, 0.3),
      new Vector4(0.9, 0.85, 0.7, 0.5)
    ),
    emissionOverTime: new ConstantValue(15),
    shape: new SphereEmitter({ radius: 2 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
  };
}

function explosionPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  return {
    material,
    looping: false,
    duration: 0.5,
    autoDestroy: true,
    startLife: new IntervalValue(0.3, 0.8),
    startSpeed: new IntervalValue(3, 8),
    startSize: new IntervalValue(0.2, 0.5),
    startColor: new ColorRange(
      new Vector4(1, 0.9, 0.3, 1),
      new Vector4(1, 0.4, 0.1, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(60),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new SphereEmitter({ radius: 0.1 }),
    worldSpace: false,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(new PiecewiseBezier([[new Bezier(1, 0.6, 0.2, 0), 0]])),
      new GravityForce(new Vector3(0, 0, 0), -5),
    ],
  };
}

function sparksPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.2, 0.6),
    startSpeed: new IntervalValue(5, 12),
    startSize: new ConstantValue(0.04),
    startColor: new ColorRange(
      new Vector4(1, 1, 0.5, 1),
      new Vector4(1, 0.8, 0.2, 1)
    ),
    emissionOverTime: new ConstantValue(30),
    shape: new ConeEmitter({ radius: 0.05, angle: Math.PI / 8 }),
    worldSpace: false,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [new GravityForce(new Vector3(0, 0, 0), -15)],
  };
}

function magicPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(0.8, 1.5),
    startSpeed: new IntervalValue(0.5, 2),
    startSize: new IntervalValue(0.1, 0.25),
    startColor: new ColorRange(
      new Vector4(0.5, 0.2, 1, 0.9),
      new Vector4(0.3, 0.5, 1, 1)
    ),
    emissionOverTime: new ConstantValue(30),
    shape: new SphereEmitter({ radius: 0.5 }),
    worldSpace: false,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(new PiecewiseBezier([[new Bezier(0.5, 1, 0.8, 0), 0]])),
      new ColorOverLife(
        new Gradient(
          [
            [new Vector3(0.6, 0.3, 1), 0],
            [new Vector3(0.3, 0.5, 1), 0.5],
            [new Vector3(0.1, 0.2, 0.6), 1],
          ],
          [
            [1, 0],
            [0.3, 1],
          ]
        )
      ),
    ],
  };
}

function firefliesPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
  });

  return {
    material,
    looping: true,
    duration: 5,
    startLife: new IntervalValue(2, 4),
    startSpeed: new IntervalValue(0.1, 0.4),
    startSize: new IntervalValue(0.04, 0.1),
    startColor: new ColorRange(
      new Vector4(0.6, 1, 0.2, 0.8),
      new Vector4(0.9, 1, 0.4, 1)
    ),
    emissionOverTime: new ConstantValue(8),
    shape: new SphereEmitter({ radius: 3 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(0.3, 1, 0.6, 0.3), 0]])
      ),
    ],
  };
}

function splashPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.9,
    depthWrite: false,
  });

  return {
    material,
    looping: false,
    duration: 0.7,
    autoDestroy: true,
    startLife: new IntervalValue(0.3, 0.6),
    startSpeed: new IntervalValue(2, 5.5),
    startSize: new IntervalValue(0.05, 0.16),
    startColor: new ColorRange(
      new Vector4(0.85, 0.95, 1, 0.9),
      new Vector4(0.6, 0.85, 1, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(45),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new ConeEmitter({ radius: 0.25, angle: Math.PI / 3.5 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [
      new GravityForce(new Vector3(0, 0, 0), -18),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.9, 0.6, 0.1), 0]])
      ),
    ],
  };
}

/** Splinters that fly off a tree with each axe blow. */
function woodchipsPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 1,
    depthWrite: false,
  });

  return {
    material,
    looping: false,
    duration: 0.9,
    autoDestroy: true,
    startLife: new IntervalValue(0.35, 0.8),
    startSpeed: new IntervalValue(2.5, 6),
    startSize: new IntervalValue(0.04, 0.14),
    startColor: new ColorRange(
      new Vector4(0.55, 0.36, 0.18, 1),
      new Vector4(0.8, 0.62, 0.38, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(25),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new ConeEmitter({ radius: 0.15, angle: Math.PI / 2.6 }),
    worldSpace: true,
    renderMode: RenderMode.StretchedBillBoard,
    behaviors: [
      new GravityForce(new Vector3(0, 0, 0), -22),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.95, 0.7, 0.2), 0]])
      ),
    ],
  };
}

/** Angular grey fragments knocked off a rock by the pickaxe. */
function rockshardsPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 1,
    depthWrite: false,
  });

  return {
    material,
    looping: false,
    duration: 0.9,
    autoDestroy: true,
    startLife: new IntervalValue(0.3, 0.7),
    startSpeed: new IntervalValue(3, 7),
    startSize: new IntervalValue(0.05, 0.16),
    startColor: new ColorRange(
      new Vector4(0.45, 0.45, 0.47, 1),
      new Vector4(0.72, 0.72, 0.75, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(22),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new ConeEmitter({ radius: 0.2, angle: Math.PI / 2.4 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new GravityForce(new Vector3(0, 0, 0), -26),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.9, 0.6, 0.15), 0]])
      ),
    ],
  };
}

/** Slow fluttering foliage puff for a felled tree's canopy. */
function leavesPreset(): Partial<SystemParams> {
  const material = new THREE.MeshBasicMaterial({
    transparent: true,
    opacity: 0.95,
    depthWrite: false,
  });

  return {
    material,
    looping: false,
    duration: 2.5,
    autoDestroy: true,
    startLife: new IntervalValue(1.2, 2.4),
    startSpeed: new IntervalValue(0.6, 2.2),
    startSize: new IntervalValue(0.08, 0.2),
    startColor: new ColorRange(
      new Vector4(0.25, 0.5, 0.15, 1),
      new Vector4(0.45, 0.7, 0.25, 1)
    ),
    emissionOverTime: new ConstantValue(0),
    emissionBursts: [
      {
        time: 0,
        count: new ConstantValue(35),
        cycle: 1,
        interval: 0.01,
        probability: 1,
      },
    ],
    shape: new SphereEmitter({ radius: 0.9, thickness: 1 }),
    worldSpace: true,
    renderMode: RenderMode.BillBoard,
    behaviors: [
      new GravityForce(new Vector3(0, 0, 0), -1.6),
      new SizeOverLife(
        new PiecewiseBezier([[new Bezier(1, 0.95, 0.8, 0.3), 0]])
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
};

export function createPresetParams(name: PresetName): Partial<SystemParams> {
  const factory = PRESET_FACTORIES[name];
  return factory ? factory() : firePreset();
}

export { PRESET_NAMES };
