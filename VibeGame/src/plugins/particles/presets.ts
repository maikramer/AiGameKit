import * as THREE from 'three';
import type { BatchedRenderer, Behavior } from 'three.quarks';
import {
  Bezier,
  ConstantColor,
  ConstantValue,
  Gradient,
  PiecewiseBezier,
} from 'three.quarks';
import {
  ColorOverLife as QuarksColorOverLife,
  SizeOverLife as QuarksSizeOverLife,
} from 'three.quarks';
import { ParticleSystem, SphereEmitter, Vector3, Vector4 } from 'three.quarks';

export interface ColorOverLifeConfig {
  startR: number;
  startG: number;
  startB: number;
  startA: number;
  endR: number;
  endG: number;
  endB: number;
  endA: number;
}

export interface SizeOverLifeConfig {
  startSize: number;
  endSize: number;
}

export type ParticlePresetId = 0 | 1 | 2 | 3 | 4 | 5 | 99;

const _tmpV3Start = new Vector3();
const _tmpV3End = new Vector3();

/** Material map keyed by ParticleSystem — used by sprite sheet animation in systems.ts. */
export const psMaterialMap = new WeakMap<
  ParticleSystem,
  THREE.MeshBasicMaterial
>();

export function createParticleSystemForPreset(
  preset: number,
  rate: number,
  lifetime: number,
  size: number,
  batch: BatchedRenderer,
  colorOverLife?: ColorOverLifeConfig,
  sizeOverLife?: SizeOverLifeConfig,
  texture?: THREE.Texture
): ParticleSystem {
  const mat = new THREE.MeshBasicMaterial({
    color: 0xff6600,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

  if (texture) {
    mat.map = texture;
    mat.alphaMap = texture;
    mat.transparent = true;
    mat.needsUpdate = true;
  }

  const base = {
    looping: true,
    duration: Math.max(0.5, lifetime),
    material: mat,
    startLife: new ConstantValue(lifetime),
    startSpeed: new ConstantValue(2 + preset * 0.1),
    startSize: new ConstantValue(size),
    startColor: new ConstantColor(new Vector4(1, 0.4, 0.1, 1)),
    emissionOverTime: new ConstantValue(rate),
    emitterShape: new SphereEmitter({ radius: 0.12 + preset * 0.02 }),
    worldSpace: true,
  };

  if (preset === 3) {
    // sparks: short-lived, small; XML `lifetime` is scaled down, not used verbatim
    const sparkLife = Math.min(Math.max(lifetime * 0.22, 0.12), 0.5);
    mat.color.setHex(0xb8d4ff);
    mat.blending = THREE.AdditiveBlending;
    base.duration = Math.max(0.35, sparkLife * 1.2);
    base.startColor = new ConstantColor(new Vector4(0.82, 0.9, 1, 0.75));
    base.startSpeed = new ConstantValue(1.25);
    base.startLife = new ConstantValue(sparkLife);
    base.startSize = new ConstantValue(Math.max(size * 0.32, 0.035));
    base.emissionOverTime = new ConstantValue(Math.max(rate * 0.4, 1));
    base.emitterShape = new SphereEmitter({ radius: 0.055 });
  } else if (preset === 1) {
    mat.color.setHex(0x888888);
    base.startColor = new ConstantColor(new Vector4(0.5, 0.5, 0.5, 0.6));
  } else if (preset === 2) {
    mat.color.setHex(0xffff44);
    base.startColor = new ConstantColor(new Vector4(1, 1, 0.3, 1));
    base.startSpeed = new ConstantValue(8);
  } else if (preset === 4) {
    // rain - fast downward, blue-white, small droplets
    mat.color.setHex(0xaaddff);
    mat.blending = THREE.NormalBlending;
    base.startColor = new ConstantColor(new Vector4(0.6, 0.8, 1, 0.7));
    base.startSpeed = new ConstantValue(15);
    base.startLife = new ConstantValue(0.6);
    base.startSize = new ConstantValue(size * 0.4);
    base.emitterShape = new SphereEmitter({ radius: 3 });
    base.emissionOverTime = new ConstantValue(rate * 4);
  } else if (preset === 5) {
    // snow - slow drift, white, low speed, long life
    mat.color.setHex(0xffffff);
    mat.blending = THREE.NormalBlending;
    base.startColor = new ConstantColor(new Vector4(1, 1, 1, 0.9));
    base.startSpeed = new ConstantValue(0.5);
    base.startLife = new ConstantValue(lifetime * 3);
    base.startSize = new ConstantValue(size * 1.5);
    base.emitterShape = new SphereEmitter({ radius: 4 });
    base.emissionOverTime = new ConstantValue(rate * 0.6);
  } else if (preset === 99) {
    // custom - neutral defaults (white, medium speed, upward)
    mat.color.setHex(0xffffff);
    mat.blending = THREE.NormalBlending;
    base.startColor = new ConstantColor(new Vector4(1, 1, 1, 1));
    base.startSpeed = new ConstantValue(3);
    base.startLife = new ConstantValue(lifetime);
    base.startSize = new ConstantValue(size);
    base.emitterShape = new SphereEmitter({ radius: 0.15 });
  }

  if (colorOverLife) {
    base.startColor = new ConstantColor(new Vector4(1, 1, 1, 1));
  }

  const behaviors: Behavior[] = [];

  const ps = new ParticleSystem(base);

  if (colorOverLife) {
    _tmpV3Start.set(
      colorOverLife.startR,
      colorOverLife.startG,
      colorOverLife.startB
    );
    _tmpV3End.set(colorOverLife.endR, colorOverLife.endG, colorOverLife.endB);
    const gradient = new Gradient(
      [
        [_tmpV3Start.clone(), 0],
        [_tmpV3End.clone(), 1],
      ],
      [
        [colorOverLife.startA, 0],
        [colorOverLife.endA, 1],
      ]
    );
    behaviors.push(new QuarksColorOverLife(gradient));
  }

  if (sizeOverLife) {
    const ratio = sizeOverLife.endSize / sizeOverLife.startSize;
    behaviors.push(
      new QuarksSizeOverLife(
        new PiecewiseBezier([
          [
            new Bezier(
              1,
              1 + (ratio - 1) / 3,
              1 + ((ratio - 1) * 2) / 3,
              ratio
            ),
            0,
          ],
        ])
      )
    );
  }

  for (const b of behaviors) {
    ps.addBehavior(b);
  }

  psMaterialMap.set(ps, mat);
  batch.addSystem(ps);
  return ps;
}
