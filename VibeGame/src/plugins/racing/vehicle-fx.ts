import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { WorldTransform } from '../transforms';
import { Vehicle } from './components';
import { conditionWetness, getRaceState } from './race-state';
import { driftTier } from './vehicle-control';

const vehicleQuery = defineQuery([Vehicle]);

/** Shared particle budget for every car on track. */
const SMOKE_COUNT = 220;
/** Ring buffer of skid quads (2 triangles each). */
const SKID_SEGMENTS = 900;
/** Boost trail segments per car (each is a small quad at the exhaust). */
const BOOST_TRAIL_COUNT = 48;
/** Drift-spark particles across every car (tier-coloured, short-lived). */
const SPARK_COUNT = 96;
/** Tier spark colours: [blue, orange] — the Mario-Kart charge tells. */
const SPARK_COLORS = [new THREE.Color('#38d1ff'), new THREE.Color('#ffb347')];

interface FxState {
  smoke: THREE.Points;
  smokePos: Float32Array;
  smokeVel: Float32Array;
  smokeLife: Float32Array;
  smokeSize: Float32Array;
  smokeAlpha: Float32Array;
  smokeCursor: number;
  skid: THREE.Mesh;
  skidPos: Float32Array;
  skidAlpha: Float32Array;
  skidCursor: number;
  /** Last wheel-contact point per vehicle, so skids draw as continuous strips. */
  lastMark: Map<number, { x: number; y: number; z: number; valid: boolean }>;
  /** Boost jet trail (one shared pool, per-car segments offset by index). */
  trail: THREE.Points;
  trailPos: Float32Array;
  trailAlpha: Float32Array;
  trailSize: Float32Array;
  /** Per-car segment cursor. */
  trailCursor: Map<number, number>;
  /** Per-car last emission position + heading (to build a strip). */
  trailPrev: Map<number, { x: number; y: number; z: number; heading: number }>;
  /** Drift charge sparks (shared pool, per-particle colour). */
  sparks: THREE.Points;
  sparkPos: Float32Array;
  sparkColor: Float32Array;
  sparkLife: Float32Array;
  sparkVel: Float32Array;
  sparkCursor: number;
}

let fx: FxState | null = null;

function buildSmokeTexture(): THREE.Texture | null {
  if (typeof document === 'undefined') return null;
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  const grad = ctx.createRadialGradient(
    size / 2,
    size / 2,
    0,
    size / 2,
    size / 2,
    size / 2
  );
  grad.addColorStop(0, 'rgba(255,255,255,0.9)');
  grad.addColorStop(0.45, 'rgba(255,255,255,0.35)');
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, size, size);
  return new THREE.CanvasTexture(canvas);
}

function createFx(scene: THREE.Object3D): FxState {
  // ---- Tyre smoke ---------------------------------------------------------
  const smokePos = new Float32Array(SMOKE_COUNT * 3);
  const smokeVel = new Float32Array(SMOKE_COUNT * 3);
  const smokeLife = new Float32Array(SMOKE_COUNT);
  const smokeSize = new Float32Array(SMOKE_COUNT);
  const alpha = new Float32Array(SMOKE_COUNT);
  for (let i = 0; i < SMOKE_COUNT; i++) smokePos[i * 3 + 1] = -9999;

  const smokeGeo = new THREE.BufferGeometry();
  smokeGeo.setAttribute('position', new THREE.BufferAttribute(smokePos, 3));
  smokeGeo.setAttribute('aAlpha', new THREE.BufferAttribute(alpha, 1));
  smokeGeo.setAttribute('aSize', new THREE.BufferAttribute(smokeSize, 1));

  const smokeMat = new THREE.ShaderMaterial({
    uniforms: { uMap: { value: buildSmokeTexture() } },
    vertexShader: `
      attribute float aAlpha;
      attribute float aSize;
      varying float vAlpha;
      void main() {
        vAlpha = aAlpha;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * (320.0 / max(1.0, -mv.z));
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      uniform sampler2D uMap;
      varying float vAlpha;
      void main() {
        vec4 tex = texture2D(uMap, gl_PointCoord);
        gl_FragColor = vec4(vec3(0.86, 0.86, 0.88), tex.a * vAlpha);
        if (gl_FragColor.a < 0.01) discard;
      }
    `,
    transparent: true,
    depthWrite: false,
  });
  const smoke = new THREE.Points(smokeGeo, smokeMat);
  smoke.frustumCulled = false;
  smoke.name = 'TyreSmoke';
  scene.add(smoke);

  // ---- Skid marks ---------------------------------------------------------
  const skidPos = new Float32Array(SKID_SEGMENTS * 6 * 3);
  const skidAlpha = new Float32Array(SKID_SEGMENTS * 6);
  const skidGeo = new THREE.BufferGeometry();
  skidGeo.setAttribute('position', new THREE.BufferAttribute(skidPos, 3));
  skidGeo.setAttribute('aAlpha', new THREE.BufferAttribute(skidAlpha, 1));
  const skidMat = new THREE.ShaderMaterial({
    vertexShader: `
      attribute float aAlpha;
      varying float vAlpha;
      void main() {
        vAlpha = aAlpha;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      varying float vAlpha;
      void main() {
        gl_FragColor = vec4(0.04, 0.04, 0.05, vAlpha * 0.55);
        if (gl_FragColor.a < 0.01) discard;
      }
    `,
    transparent: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
  });
  const skid = new THREE.Mesh(skidGeo, skidMat);
  skid.frustumCulled = false;
  skid.name = 'SkidMarks';
  scene.add(skid);

  // ---- Boost jet trail -----------------------------------------------------
  // A shared Points pool; each vehicle owns a stride of segments. When the car
  // boosts, we emit a quad per frame at the exhaust and fade the whole strip.
  const trailPos = new Float32Array(BOOST_TRAIL_COUNT * 3);
  const trailAlpha = new Float32Array(BOOST_TRAIL_COUNT);
  const trailSize = new Float32Array(BOOST_TRAIL_COUNT);
  for (let i = 0; i < BOOST_TRAIL_COUNT; i++) {
    trailPos[i * 3 + 1] = -9999;
    trailSize[i] = 0.35;
  }
  const trailGeo = new THREE.BufferGeometry();
  trailGeo.setAttribute('position', new THREE.BufferAttribute(trailPos, 3));
  trailGeo.setAttribute('aAlpha', new THREE.BufferAttribute(trailAlpha, 1));
  trailGeo.setAttribute('aSize', new THREE.BufferAttribute(trailSize, 1));
  const trailMat = new THREE.ShaderMaterial({
    vertexShader: `
      attribute float aAlpha;
      attribute float aSize;
      varying float vAlpha;
      void main() {
        vAlpha = aAlpha;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = aSize * (300.0 / max(1.0, -mv.z));
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      varying float vAlpha;
      void main() {
        vec2 d = gl_PointCoord - vec2(0.5);
        float r = length(d);
        float core = smoothstep(0.5, 0.0, r);
        gl_FragColor = vec4(0.2, 0.85, 1.0, core * vAlpha);
        if (gl_FragColor.a < 0.01) discard;
      }
    `,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const trail = new THREE.Points(trailGeo, trailMat);
  trail.frustumCulled = false;
  trail.name = 'BoostTrails';
  scene.add(trail);

  // ---- Drift-charge sparks --------------------------------------------------
  // Same Points trick as the trail, plus a per-particle colour: blue at the
  // first charge tier, orange at the second — the driver's only feedback that
  // the drift is loaded, so it has to be readable at race speed.
  const sparkPos = new Float32Array(SPARK_COUNT * 3);
  const sparkColor = new Float32Array(SPARK_COUNT * 3);
  const sparkLife = new Float32Array(SPARK_COUNT);
  const sparkVel = new Float32Array(SPARK_COUNT * 3);
  for (let i = 0; i < SPARK_COUNT; i++) sparkPos[i * 3 + 1] = -9999;
  const sparkGeo = new THREE.BufferGeometry();
  sparkGeo.setAttribute('position', new THREE.BufferAttribute(sparkPos, 3));
  sparkGeo.setAttribute('aColor', new THREE.BufferAttribute(sparkColor, 3));
  sparkGeo.setAttribute('aLife', new THREE.BufferAttribute(sparkLife, 1));
  const sparkMat = new THREE.ShaderMaterial({
    vertexShader: `
      attribute vec3 aColor;
      attribute float aLife;
      varying vec3 vColor;
      varying float vLife;
      void main() {
        vColor = aColor;
        vLife = aLife;
        vec4 mv = modelViewMatrix * vec4(position, 1.0);
        gl_PointSize = (2.5 + 3.5 * aLife) * (300.0 / max(1.0, -mv.z));
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      varying vec3 vColor;
      varying float vLife;
      void main() {
        vec2 d = gl_PointCoord - vec2(0.5);
        float r = length(d);
        float core = smoothstep(0.5, 0.0, r);
        vec3 c = mix(vColor, vec3(1.0), core * 0.6);
        gl_FragColor = vec4(c, core * vLife);
        if (gl_FragColor.a < 0.01) discard;
      }
    `,
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
  });
  const sparks = new THREE.Points(sparkGeo, sparkMat);
  sparks.frustumCulled = false;
  sparks.name = 'DriftSparks';
  scene.add(sparks);

  return {
    smoke,
    smokePos,
    smokeVel,
    smokeLife,
    smokeSize,
    smokeAlpha: alpha,
    smokeCursor: 0,
    skid,
    skidPos,
    skidAlpha,
    skidCursor: 0,
    lastMark: new Map(),
    trail,
    trailPos,
    trailAlpha,
    trailSize,
    trailCursor: new Map(),
    trailPrev: new Map(),
    sparks,
    sparkPos,
    sparkColor,
    sparkLife,
    sparkVel,
    sparkCursor: 0,
  };
}

/** One drift spark at a rear wheel, tinted by the charge tier. */
function emitSpark(
  f: FxState,
  x: number,
  y: number,
  z: number,
  color: THREE.Color
): void {
  const i = f.sparkCursor;
  f.sparkCursor = (f.sparkCursor + 1) % SPARK_COUNT;
  f.sparkPos[i * 3] = x + (Math.random() - 0.5) * 0.3;
  f.sparkPos[i * 3 + 1] = y + 0.12;
  f.sparkPos[i * 3 + 2] = z + (Math.random() - 0.5) * 0.3;
  f.sparkVel[i * 3] = (Math.random() - 0.5) * 2.6;
  f.sparkVel[i * 3 + 1] = 0.8 + Math.random() * 2.4;
  f.sparkVel[i * 3 + 2] = (Math.random() - 0.5) * 2.6;
  f.sparkColor[i * 3] = color.r;
  f.sparkColor[i * 3 + 1] = color.g;
  f.sparkColor[i * 3 + 2] = color.b;
  f.sparkLife[i] = 1;
}

function emitSmoke(
  f: FxState,
  x: number,
  y: number,
  z: number,
  strength: number
): void {
  const i = f.smokeCursor;
  f.smokeCursor = (f.smokeCursor + 1) % SMOKE_COUNT;
  f.smokePos[i * 3] = x + (Math.random() - 0.5) * 0.4;
  f.smokePos[i * 3 + 1] = y + 0.1;
  f.smokePos[i * 3 + 2] = z + (Math.random() - 0.5) * 0.4;
  f.smokeVel[i * 3] = (Math.random() - 0.5) * 1.4;
  f.smokeVel[i * 3 + 1] = 0.7 + Math.random() * 1.1;
  f.smokeVel[i * 3 + 2] = (Math.random() - 0.5) * 1.4;
  f.smokeLife[i] = 0.5 + strength * 0.8;
  f.smokeSize[i] = 0.5 + strength * 0.9;
}

function pushSkid(
  f: FxState,
  ax: number,
  ay: number,
  az: number,
  bx: number,
  by: number,
  bz: number,
  width: number,
  alpha: number
): void {
  // Quad from a→b, widened across the travel direction.
  const dx = bx - ax;
  const dz = bz - az;
  const len = Math.hypot(dx, dz) || 1;
  const nx = (-dz / len) * width * 0.5;
  const nz = (dx / len) * width * 0.5;
  const i = f.skidCursor;
  f.skidCursor = (f.skidCursor + 1) % SKID_SEGMENTS;
  const base = i * 18;
  const verts = [
    ax + nx,
    ay,
    az + nz,
    ax - nx,
    ay,
    az - nz,
    bx + nx,
    by,
    bz + nz,
    ax - nx,
    ay,
    az - nz,
    bx - nx,
    by,
    bz - nz,
    bx + nx,
    by,
    bz + nz,
  ];
  for (let k = 0; k < 18; k++) f.skidPos[base + k] = verts[k]!;
  for (let k = 0; k < 6; k++) f.skidAlpha[i * 6 + k] = alpha;
}

/**
 * Tyre smoke and skid marks, driven by the controller's slip value.
 *
 * One shared particle pool and one shared skid ring buffer serve every car on
 * track, so a full grid costs two draw calls — the previous build allocated a
 * separate particle system, trail ribbon and shader pass *per vehicle*.
 */
export const VehicleFxSystem: System = defineSystem({
  name: 'VehicleFxSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;
    if (!fx) fx = createFx(scene as THREE.Object3D);
    const f = fx;
    const dt = Math.min(state.time.deltaTime, 0.05);

    // ---- Emit from sliding tyres -----------------------------------------
    for (const eid of vehicleQuery(state.world)) {
      const slip = Vehicle.slip[eid];
      const speed = Math.abs(Vehicle.speed[eid]);
      const sliding = slip > 0.18 && speed > 4 && Vehicle.airborne[eid] === 0;

      const x = WorldTransform.posX[eid];
      const y = WorldTransform.posY[eid];
      const z = WorldTransform.posZ[eid];
      const heading = Vehicle.heading[eid];
      const rearX = x - Math.sin(heading) * 1.1;
      const rearZ = z - Math.cos(heading) * 1.1;
      const rideY = y - (Vehicle.rideHeight[eid] || 0.35) + 0.02;

      // ---- Boost jet trail (also lit while a mini-turbo burst pays out) ----
      const bursting =
        Vehicle.boosting[eid] === 1 || (Vehicle.miniTurbo[eid] ?? 0) > 0;
      if (bursting) {
        const prev = f.trailPrev.get(eid);
        let cursor = f.trailCursor.get(eid) ?? 0;
        // Emit every frame while boosting; skip if the car has not moved since
        // the last frame (avoids piling segments at the same spot).
        const moved =
          !prev ||
          Math.hypot(prev.x - rearX, prev.z - rearZ) > 0.2 ||
          Math.abs(prev.heading - heading) > 0.05;
        if (moved) {
          const i = cursor;
          cursor = (cursor + 1) % BOOST_TRAIL_COUNT;
          f.trailPos[i * 3] = rearX + (Math.random() - 0.5) * 0.2;
          f.trailPos[i * 3 + 1] = rideY + 0.35;
          f.trailPos[i * 3 + 2] = rearZ + (Math.random() - 0.5) * 0.2;
          f.trailAlpha[i] = 1;
          f.trailSize[i] = 0.5 + Math.random() * 0.4;
          f.trailCursor.set(eid, cursor);
        }
        f.trailPrev.set(eid, { x: rearX, y: rideY, z: rearZ, heading });
      } else {
        f.trailPrev.delete(eid);
        f.trailCursor.delete(eid);
      }

      // ---- Drift-charge sparks ----------------------------------------------
      // From the first tier on, the charged tyres throw coloured sparks at
      // both rear wheels: the colour is the tier, the rate is the charge.
      const charge = Vehicle.driftCharge[eid] ?? 0;
      const tier = driftTier(charge);
      if (tier > 0 && speed > 4 && Vehicle.airborne[eid] === 0) {
        const color = SPARK_COLORS[tier - 1]!;
        const rate = tier === 2 ? 0.85 : 0.55;
        const rightX = Math.cos(heading);
        const rightZ = -Math.sin(heading);
        for (const side of [-1, 1]) {
          if (Math.random() < rate * dt * 60) {
            emitSpark(
              f,
              rearX + rightX * side * 0.7,
              rideY,
              rearZ + rightZ * side * 0.7,
              color
            );
          }
        }
      }

      // A botched launch: tyres lit up from a standstill.
      if ((Vehicle.wheelspin[eid] ?? 0) > 0 && Math.random() < 0.75) {
        emitSmoke(f, rearX, rideY, rearZ, 0.4);
      }

      const prev = f.lastMark.get(eid);
      if (sliding) {
        const strength = Math.min(1, slip * 1.4);
        if (Math.random() < strength * 0.9)
          emitSmoke(f, rearX, rideY, rearZ, strength);
        if (prev?.valid) {
          const moved = Math.hypot(rearX - prev.x, rearZ - prev.z);
          if (moved > 0.35) {
            pushSkid(
              f,
              prev.x,
              prev.y,
              prev.z,
              rearX,
              rideY,
              rearZ,
              1.5,
              Math.min(1, slip * 1.6)
            );
            f.lastMark.set(eid, { x: rearX, y: rideY, z: rearZ, valid: true });
          }
        } else {
          f.lastMark.set(eid, { x: rearX, y: rideY, z: rearZ, valid: true });
        }
      } else if (prev) {
        prev.valid = false;
      }

      const spray = conditionWetness(getRaceState().condition);
      if (
        spray > 0.3 &&
        speed > 8 &&
        Vehicle.airborne[eid] === 0 &&
        Math.random() < 0.45 * spray
      ) {
        emitSmoke(f, rearX, rideY, rearZ, 0.35 + spray * 0.4);
      }
    }

    // ---- Integrate the smoke ---------------------------------------------
    const alphaAttr = f.smoke.geometry.getAttribute(
      'aAlpha'
    ) as THREE.BufferAttribute;
    for (let i = 0; i < SMOKE_COUNT; i++) {
      const life = f.smokeLife[i] ?? 0;
      if (life <= 0) {
        f.smokeAlpha[i] = 0;
        continue;
      }
      const remaining = life - dt;
      f.smokeLife[i] = remaining;
      const px = f.smokePos[i * 3] ?? 0;
      const py = f.smokePos[i * 3 + 1] ?? 0;
      const pz = f.smokePos[i * 3 + 2] ?? 0;
      const vy = f.smokeVel[i * 3 + 1] ?? 0;
      f.smokePos[i * 3] = px + (f.smokeVel[i * 3] ?? 0) * dt;
      f.smokePos[i * 3 + 1] = py + vy * dt;
      f.smokePos[i * 3 + 2] = pz + (f.smokeVel[i * 3 + 2] ?? 0) * dt;
      f.smokeVel[i * 3 + 1] = vy * (1 - 0.9 * dt);
      f.smokeSize[i] = (f.smokeSize[i] ?? 0) + dt * 1.4;
      f.smokeAlpha[i] = Math.max(0, Math.min(1, remaining)) * 0.5;
    }
    f.smoke.geometry.getAttribute('position').needsUpdate = true;
    f.smoke.geometry.getAttribute('aSize').needsUpdate = true;
    alphaAttr.needsUpdate = true;

    // ---- Fade the skid marks ---------------------------------------------
    const skidAlpha = f.skid.geometry.getAttribute(
      'aAlpha'
    ) as THREE.BufferAttribute;
    const fade = dt * 0.06;
    let dirty = false;
    for (let i = 0; i < f.skidAlpha.length; i++) {
      if (f.skidAlpha[i]! > 0) {
        f.skidAlpha[i] = Math.max(0, f.skidAlpha[i]! - fade);
        dirty = true;
      }
    }
    if (dirty) {
      f.skid.geometry.getAttribute('position').needsUpdate = true;
      skidAlpha.needsUpdate = true;
    }

    // ---- Fade the boost trail ---------------------------------------------
    const trailAlphaAttr = f.trail.geometry.getAttribute(
      'aAlpha'
    ) as THREE.BufferAttribute;
    const trailFade = dt * 2.2;
    let trailDirty = false;
    for (let i = 0; i < f.trailAlpha.length; i++) {
      if (f.trailAlpha[i]! > 0) {
        f.trailAlpha[i] = Math.max(0, f.trailAlpha[i]! - trailFade);
        trailDirty = true;
      }
    }
    if (trailDirty) {
      f.trail.geometry.getAttribute('position').needsUpdate = true;
      trailAlphaAttr.needsUpdate = true;
    }

    // ---- Integrate the drift sparks -----------------------------------------
    let sparkDirty = false;
    for (let i = 0; i < SPARK_COUNT; i++) {
      const life = f.sparkLife[i] ?? 0;
      if (life <= 0) continue;
      f.sparkLife[i] = Math.max(0, life - dt * 2.8);
      f.sparkPos[i * 3] += (f.sparkVel[i * 3] ?? 0) * dt;
      f.sparkPos[i * 3 + 1] += (f.sparkVel[i * 3 + 1] ?? 0) * dt;
      f.sparkPos[i * 3 + 2] += (f.sparkVel[i * 3 + 2] ?? 0) * dt;
      f.sparkVel[i * 3 + 1] = (f.sparkVel[i * 3 + 1] ?? 0) - 5.5 * dt;
      sparkDirty = true;
    }
    if (sparkDirty) {
      f.sparks.geometry.getAttribute('position').needsUpdate = true;
      (
        f.sparks.geometry.getAttribute('aColor') as THREE.BufferAttribute
      ).needsUpdate = true;
      (
        f.sparks.geometry.getAttribute('aLife') as THREE.BufferAttribute
      ).needsUpdate = true;
    }
  },

  dispose() {
    if (!fx) return;
    fx.smoke.parent?.remove(fx.smoke);
    fx.skid.parent?.remove(fx.skid);
    fx.trail.parent?.remove(fx.trail);
    fx.sparks.parent?.remove(fx.sparks);
    fx.smoke.geometry.dispose();
    (fx.smoke.material as THREE.Material).dispose();
    fx.skid.geometry.dispose();
    (fx.skid.material as THREE.Material).dispose();
    fx.trail.geometry.dispose();
    (fx.trail.material as THREE.Material).dispose();
    fx.sparks.geometry.dispose();
    (fx.sparks.material as THREE.Material).dispose();
    fx.trailCursor.clear();
    fx.trailPrev.clear();
    fx.lastMark.clear();
    fx = null;
  },
});
