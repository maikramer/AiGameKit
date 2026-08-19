import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import {
  createGLTFLoader,
  ensureKTX2LoaderReady,
} from '../../extras/gltf-bridge';
import { logger } from '../../core/utils/logger';
import { fitModel } from '../../extras/model-fit';
import { WorldTransform } from '../transforms';
import {
  Vehicle,
  VehicleColors,
  VehicleModelLength,
  VehicleModelUrls,
  VehicleModelYaw,
  HeldItem,
} from './components';
import { conditionIsNight, getRaceState } from './race-state';

const visualQuery = defineQuery([Vehicle]);
const built = new Map<number, ChassisVisual>();

interface ChassisVisual {
  /** Root, driven by the entity world transform. */
  group: THREE.Group;
  /** Child pivot carrying roll/pitch so juice never fights the physics pose. */
  pivot: THREE.Group;
  procedural: THREE.Group;
  wheels: THREE.Object3D[];
  steerWheels: THREE.Object3D[];
  brakeLights: THREE.Mesh[];
  exhaust: THREE.Mesh | null;
  shadow: THREE.Mesh;
  modelRoot: THREE.Group | null;
  headlights: THREE.SpotLight[];
  shield: THREE.Mesh;
}

const WHEEL_COLOR = 0x141416;
const RIM_COLOR = 0xb9bec8;
const GLASS_COLOR = 0x9fd0ff;

/** Default chassis length used to normalise generated GLBs (m). */
const DEFAULT_MODEL_LENGTH = 2.6;

function makeMaterial(
  color: number,
  options: Partial<THREE.MeshStandardMaterialParameters> = {}
) {
  return new THREE.MeshStandardMaterial({
    color,
    roughness: 0.45,
    metalness: 0.25,
    ...options,
  });
}

/**
 * Two-layer car paint: a pigmented base under a thin clear lacquer.
 *
 * A single `MeshStandardMaterial` has one specular lobe, so making paint shiny
 * means making the *pigment* shiny — and metallic red plastic is what that
 * looks like. `MeshPhysicalMaterial`'s clearcoat adds a second, sharper lobe
 * with its own roughness on top of the base, which is physically what a
 * lacquered panel is: the colour stays matte-ish and the sky reflection rides
 * over it. It is also what makes a car read as a *car* the instant an
 * environment map exists.
 */
function makeCarPaint(color: number): THREE.MeshPhysicalMaterial {
  return new THREE.MeshPhysicalMaterial({
    color,
    roughness: 0.38,
    metalness: 0.15,
    clearcoat: 1,
    clearcoatRoughness: 0.08,
    envMapIntensity: 1.15,
  });
}

/** A readable low-poly kart, used when no GLB is supplied (or while it loads). */
function buildProceduralChassis(color: number): {
  group: THREE.Group;
  wheels: THREE.Object3D[];
  steerWheels: THREE.Object3D[];
  brakeLights: THREE.Mesh[];
  exhaust: THREE.Mesh;
} {
  const group = new THREE.Group();
  const bodyMat = makeCarPaint(color);
  const darkMat = makeMaterial(0x1b1d22, { roughness: 0.6, metalness: 0.2 });

  const hull = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.42, 3.1), bodyMat);
  hull.position.y = 0.44;
  hull.castShadow = true;
  group.add(hull);

  const nose = new THREE.Mesh(new THREE.BoxGeometry(1.15, 0.26, 0.9), bodyMat);
  nose.position.set(0, 0.34, 1.75);
  nose.castShadow = true;
  group.add(nose);

  const cockpit = new THREE.Mesh(
    new THREE.BoxGeometry(1.05, 0.42, 1.1),
    darkMat
  );
  cockpit.position.set(0, 0.76, -0.15);
  cockpit.castShadow = true;
  group.add(cockpit);

  const screen = new THREE.Mesh(
    new THREE.BoxGeometry(0.95, 0.3, 0.06),
    makeMaterial(GLASS_COLOR, {
      roughness: 0.1,
      metalness: 0.1,
      transparent: true,
      opacity: 0.55,
    })
  );
  screen.position.set(0, 0.82, 0.42);
  screen.rotation.x = -0.35;
  group.add(screen);

  const wing = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.08, 0.42), darkMat);
  wing.position.set(0, 0.95, -1.6);
  wing.castShadow = true;
  group.add(wing);
  for (const side of [-1, 1]) {
    const strut = new THREE.Mesh(
      new THREE.BoxGeometry(0.09, 0.34, 0.12),
      darkMat
    );
    strut.position.set(side * 0.6, 0.78, -1.6);
    group.add(strut);
  }

  const brakeLights: THREE.Mesh[] = [];
  for (const side of [-1, 1]) {
    const light = new THREE.Mesh(
      new THREE.BoxGeometry(0.34, 0.12, 0.06),
      new THREE.MeshStandardMaterial({
        color: 0x6a0d12,
        emissive: 0xff2233,
        emissiveIntensity: 0.15,
        roughness: 0.4,
      })
    );
    light.position.set(side * 0.45, 0.52, -1.57);
    group.add(light);
    brakeLights.push(light);
  }

  const exhaust = new THREE.Mesh(
    new THREE.ConeGeometry(0.16, 0.9, 8, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0x66ccff,
      transparent: true,
      opacity: 0,
      depthWrite: false,
      side: THREE.DoubleSide,
    })
  );
  exhaust.rotation.x = Math.PI / 2;
  exhaust.position.set(0, 0.45, -1.9);
  group.add(exhaust);

  const wheels: THREE.Object3D[] = [];
  const steerWheels: THREE.Object3D[] = [];
  const tyreGeo = new THREE.CylinderGeometry(0.34, 0.34, 0.3, 14);
  tyreGeo.rotateZ(Math.PI / 2);
  const rimGeo = new THREE.CylinderGeometry(0.19, 0.19, 0.32, 10);
  rimGeo.rotateZ(Math.PI / 2);
  const tyreMat = makeMaterial(WHEEL_COLOR, { roughness: 0.9, metalness: 0 });
  const rimMat = makeMaterial(RIM_COLOR, { roughness: 0.3, metalness: 0.8 });

  for (const [sx, sz, front] of [
    [-1, 1, true],
    [1, 1, true],
    [-1, -1, false],
    [1, -1, false],
  ] as [number, number, boolean][]) {
    // Steering pivot → spin pivot → mesh, so steer and roll never mix.
    const steerPivot = new THREE.Group();
    steerPivot.position.set(sx * 0.78, 0.34, sz * 1.12);
    const spin = new THREE.Group();
    const tyre = new THREE.Mesh(tyreGeo, tyreMat);
    tyre.castShadow = true;
    const rim = new THREE.Mesh(rimGeo, rimMat);
    rim.position.x = sx * 0.02;
    spin.add(tyre, rim);
    steerPivot.add(spin);
    group.add(steerPivot);
    wheels.push(spin);
    if (front) steerWheels.push(steerPivot);
  }

  return { group, wheels, steerWheels, brakeLights, exhaust };
}

/**
 * Radial falloff used by the contact shadow. A flat quad with a constant alpha
 * reads as a grey rectangle painted on the track — the single most obvious
 * "this is a toy" tell in a chase-cam shot. This gradient is opaque under the
 * chassis and reaches zero at the rim, so what is left is a soft darkening
 * that only reinforces the real shadow map instead of competing with it.
 */
let contactShadowTexture: THREE.Texture | null = null;

function getContactShadowTexture(): THREE.Texture {
  if (contactShadowTexture) return contactShadowTexture;
  const size = 128;
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d')!;
  const gradient = ctx.createRadialGradient(
    size / 2,
    size / 2,
    0,
    size / 2,
    size / 2,
    size / 2
  );
  // White RGB, falling alpha: MeshBasicMaterial multiplies `map.rgb` by the
  // material colour (black here) and `map.a` by the opacity, so the texture
  // has to carry the shape in its alpha channel, not in its luminance.
  // Quadratic-ish falloff: solid core, long soft tail — a penumbra, not a disc.
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.35, 'rgba(255,255,255,0.92)');
  gradient.addColorStop(0.62, 'rgba(255,255,255,0.5)');
  gradient.addColorStop(0.85, 'rgba(255,255,255,0.14)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, size, size);
  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.NoColorSpace;
  contactShadowTexture = tex;
  return tex;
}

/**
 * Ambient-occlusion contact patch under the car. It is NOT the car's shadow —
 * the sun's shadow map draws that — it is the darkening a body this close to
 * the ground occludes out of the sky light, which no 64 m shadow cascade
 * resolves. Kept subtle on purpose: doubled up with the real shadow it would
 * read as a smear.
 */
function buildShadow(): THREE.Mesh {
  const geo = new THREE.PlaneGeometry(2.9, 4.1);
  geo.rotateX(-Math.PI / 2);
  const mat = new THREE.MeshBasicMaterial({
    color: 0x000000,
    map: getContactShadowTexture(),
    transparent: true,
    opacity: CONTACT_SHADOW_OPACITY,
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.y = 0.02;
  mesh.renderOrder = -1;
  return mesh;
}

/** Peak alpha of the contact patch directly under a grounded chassis. */
const CONTACT_SHADOW_OPACITY = 0.55;

/**
 * Normalise a generated GLB into a chassis the game can use.
 *
 * Delegates to {@link fitModel}, which measures the model's heading from its
 * own geometry (a PCA of the vertex cloud) rather than from its bounding box.
 * A kart measures 2.38 m across and 2.69 m long, so a box-based guess is a coin
 * flip — and a wrong flip is a car that drives sideways down the straight.
 */
function normaliseModel(
  root: THREE.Object3D,
  targetLength: number,
  yawDeg: number
): void {
  fitModel(root, {
    align: 'forward',
    fit: 'length',
    size: targetLength,
    yawDegrees: yawDeg,
    ground: true,
    // Wagons are long enough (L/W ≈ 2) that PCA would auto-yaw them — and then
    // `model-yaw` would stack a second quarter-turn. Heading is author-only.
    minElongation: 99,
    standUp: 'auto',
  });
}

const WHEEL_NAME = /wheel|tyre|tire|rim/i;

/**
 * Draws every vehicle: a procedural kart by default, or a normalised GLB when
 * one is supplied, plus the juice the controller computes (wheel spin, steering
 * angle, body roll/pitch, brake lights, boost flame, contact shadow).
 */
export const VehicleVisualSystem: System = defineSystem({
  name: 'VehicleVisualSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;

    for (const eid of visualQuery(state.world)) {
      let v = built.get(eid);
      if (!v) {
        // Chassis GLBs from the asset pipeline ship KTX2 textures; the loader
        // needs the transcoder wired from the live renderer before the first
        // load, or every model fails with "setKTX2Loader must be called".
        ensureKTX2LoaderReady(state);
        v = createVisual(eid);
        built.set(eid, v);
        scene.add(v.group);
      }

      v.group.position.set(
        WorldTransform.posX[eid],
        WorldTransform.posY[eid],
        WorldTransform.posZ[eid]
      );
      v.group.quaternion.set(
        WorldTransform.rotX[eid],
        WorldTransform.rotY[eid],
        WorldTransform.rotZ[eid],
        WorldTransform.rotW[eid]
      );

      v.pivot.rotation.z = Vehicle.roll[eid];
      v.pivot.rotation.x = Vehicle.pitch[eid];

      // Stunt rotation: applied on top of the juice pivot, so a completed
      // rotation (2π) lands back on the identity pose exactly.
      if (Vehicle.trickActive[eid] === 1) {
        const spin = Vehicle.trickSpin[eid] ?? 0;
        const kind = Vehicle.trickKind[eid] ?? 0;
        if (kind === 1) v.pivot.rotation.z += spin;
        else if (kind === 2) v.pivot.rotation.z -= spin;
        else if (kind === 3) v.pivot.rotation.x -= spin;
        else v.group.rotateY(spin);
      }
      // Spin-out: two full turns over the duration, then straight again.
      const spinOut = Vehicle.spinOutTimer[eid] ?? 0;
      if (spinOut > 0) {
        const total = Vehicle.spinOutTotal[eid] || 1.15;
        v.group.rotateY((1 - spinOut / total) * Math.PI * 4);
      }

      const spin = Vehicle.wheelSpin[eid];
      for (const wheel of v.wheels) wheel.rotation.x = spin;
      for (const steer of v.steerWheels)
        steer.rotation.y = -Vehicle.wheelSteer[eid];

      const braking =
        Vehicle.brakeInput[eid] > 0 || Vehicle.handbrake[eid] === 1;
      for (const light of v.brakeLights) {
        const mat = light.material as THREE.MeshStandardMaterial;
        mat.emissiveIntensity = braking ? 2.4 : 0.15;
      }

      if (v.exhaust) {
        const mat = v.exhaust.material as THREE.MeshBasicMaterial;
        const want = Vehicle.boosting[eid] ? 0.75 : 0;
        mat.opacity += (want - mat.opacity) * 0.25;
        v.exhaust.visible = mat.opacity > 0.02;
        if (v.exhaust.visible) {
          const flicker = 0.85 + Math.random() * 0.35;
          v.exhaust.scale.set(flicker, 1, flicker);
        }
      }

      // The contact shadow stays on the ground when the car jumps.
      const air = Math.max(
        0,
        Vehicle.airHeight[eid] - (Vehicle.rideHeight[eid] || 0.35)
      );
      v.shadow.position.y = 0.02 - air;
      const shadowMat = v.shadow.material as THREE.MeshBasicMaterial;
      shadowMat.opacity = Math.max(0.05, CONTACT_SHADOW_OPACITY - air * 0.09);
      // A car in the air occludes less sky over a wider footprint — the patch
      // spreads as it fades, the way a real penumbra opens up with distance.
      const spread = 1 + Math.min(air, 3) * 0.22;
      v.shadow.scale.set(spread, 1, spread);

      const lampsOn = conditionIsNight(getRaceState().condition);
      for (const lamp of v.headlights) {
        lamp.intensity = lampsOn ? 9.5 : 0;
        lamp.visible = lampsOn;
      }

      const armed =
        state.hasComponent(eid, HeldItem) && HeldItem.shieldArmed[eid] === 1;
      v.shield.visible = armed;
      if (armed) {
        const pulse = 0.18 + Math.sin(state.time.elapsed * 10) * 0.07;
        (v.shield.material as THREE.MeshBasicMaterial).opacity = pulse;
        const s = 1 + Math.sin(state.time.elapsed * 7) * 0.05;
        v.shield.scale.setScalar(s);
      }
    }
  },

  dispose() {
    for (const v of built.values()) {
      v.group.parent?.remove(v.group);
      v.group.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.geometry?.dispose();
          const mat = mesh.material as THREE.Material | THREE.Material[];
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat?.dispose();
        }
      });
    }
    built.clear();
  },
});

function attachHeadlights(group: THREE.Group): THREE.SpotLight[] {
  const lamps: THREE.SpotLight[] = [];
  for (const x of [-0.48, 0.48]) {
    const lamp = new THREE.SpotLight(0xfff1c8, 0, 42, 0.38, 0.5, 1.15);
    lamp.position.set(x, 0.52, 1.2);
    lamp.target.position.set(x * 0.4, 0.05, 14);
    lamp.castShadow = false;
    group.add(lamp);
    group.add(lamp.target);
    lamps.push(lamp);
  }
  return lamps;
}

function buildShieldBubble(): THREE.Mesh {
  const mesh = new THREE.Mesh(
    new THREE.SphereGeometry(1.65, 18, 14),
    new THREE.MeshBasicMaterial({
      color: 0x7fd4ff,
      transparent: true,
      opacity: 0.2,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
    })
  );
  mesh.position.y = 0.7;
  mesh.visible = false;
  mesh.name = 'ShieldBubble';
  return mesh;
}

function createVisual(eid: number): ChassisVisual {
  const group = new THREE.Group();
  group.name = `Vehicle:${eid}`;
  const pivot = new THREE.Group();
  group.add(pivot);

  const color = VehicleColors.get(eid) ?? 0xcc2233;
  const proc = buildProceduralChassis(color);
  pivot.add(proc.group);

  const shadow = buildShadow();
  group.add(shadow);
  const shield = buildShieldBubble();
  pivot.add(shield);

  const visual: ChassisVisual = {
    group,
    pivot,
    procedural: proc.group,
    wheels: proc.wheels,
    steerWheels: proc.steerWheels,
    brakeLights: proc.brakeLights,
    exhaust: proc.exhaust,
    shadow,
    modelRoot: null,
    headlights: attachHeadlights(group),
    shield,
  };

  const url = VehicleModelUrls.get(eid);
  if (url) loadModel(visual, eid, url);
  return visual;
}

function loadModel(visual: ChassisVisual, eid: number, url: string): void {
  createGLTFLoader()
    .loadAsync(url)
    .then((gltf) => {
      const root = gltf.scene;
      normaliseModel(
        root,
        VehicleModelLength.get(eid) ?? DEFAULT_MODEL_LENGTH,
        VehicleModelYaw.get(eid) ?? 0
      );
      root.traverse((o) => {
        const mesh = o as THREE.Mesh;
        if (mesh.isMesh) {
          mesh.castShadow = true;
          mesh.receiveShadow = true;
        }
      });

      // The GLB replaces the procedural kart outright. Mixing the two — a
      // generated chassis sitting on top of four procedural wheels — is how a
      // car ends up with eight wheels and two bodies in the same place.
      const modelWheels: THREE.Object3D[] = [];
      root.traverse((o) => {
        if (WHEEL_NAME.test(o.name)) modelWheels.push(o);
      });

      // Keep the boost flame: re-parent it to the pivot and sit it behind the
      // model's own tail so nitro still reads on a GLB chassis.
      const modelBox = new THREE.Box3().setFromObject(root);
      if (visual.exhaust) {
        visual.pivot.add(visual.exhaust);
        visual.exhaust.position.set(
          0,
          modelBox.max.y * 0.45,
          modelBox.min.z - 0.15
        );
      }

      visual.procedural.visible = false;
      visual.procedural.parent?.remove(visual.procedural);
      visual.brakeLights = [];
      visual.steerWheels = [];
      // Only nodes the model itself provides can spin; a bodyshell with the
      // wheels modelled into it simply has none, and that is fine.
      visual.wheels = modelWheels;

      visual.pivot.add(root);
      visual.modelRoot = root;
    })
    .catch((err) => {
      // Keep the procedural chassis — a missing GLB must not delete the car —
      // but say so, because a silently ignored model is how the old example
      // shipped "working" with none of its assets on screen.
      logger.warn(`[Racing] chassis GLB failed to load: ${url}`, err);
    });
}
