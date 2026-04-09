import * as THREE from 'three';
import type { BatchedRenderer } from 'three.quarks';
import {
  ConstantColor,
  ConstantValue,
  ParticleSystem,
  SphereEmitter,
} from 'three.quarks';
import { Vector4 } from 'three.quarks';

export type ParticlePresetId = 0 | 1 | 2 | 3 | 4 | 5 | 99;

export function createParticleSystemForPreset(
  preset: number,
  rate: number,
  lifetime: number,
  size: number,
  batch: BatchedRenderer
): ParticleSystem {
  const mat = new THREE.MeshBasicMaterial({
    color: 0xff6600,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });

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

  if (preset === 1) {
    mat.color.setHex(0x888888);
    base.startColor = new ConstantColor(new Vector4(0.5, 0.5, 0.5, 0.6));
  } else if (preset === 2) {
    mat.color.setHex(0xffff44);
    base.startColor = new ConstantColor(new Vector4(1, 1, 0.3, 1));
    base.startSpeed = new ConstantValue(8);
  } else if (preset === 3) {
    // sparks - bright yellow/white
    mat.color.setHex(0xffffaa);
    base.startColor = new ConstantColor(new Vector4(1, 1, 0.8, 1));
    base.startSpeed = new ConstantValue(5);
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

  const ps = new ParticleSystem(base);
  batch.addSystem(ps);
  return ps;
}
