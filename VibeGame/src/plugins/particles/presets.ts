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
  | 'fireflies';

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
};

export function createPresetParams(name: PresetName): Partial<SystemParams> {
  const factory = PRESET_FACTORIES[name];
  return factory ? factory() : firePreset();
}

export { PRESET_NAMES };
