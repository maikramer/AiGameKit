import * as THREE from 'three';
import { defineSystem, defineQuery, type State, type System } from '../../core';
import { getScene } from '../rendering';
import { createGLTFLoader } from '../../extras/gltf-bridge';
import { WorldTransform } from '../transforms';
import { Vehicle, VehicleModelUrls, VehicleModelYaw } from './components';

const visualQuery = defineQuery([Vehicle]);
const built = new Map<number, ChassisVisual>();

interface ChassisVisual {
  group: THREE.Group;
  body: THREE.Mesh;
  wheels: THREE.Mesh[];
  rollPivot: THREE.Group;
  /** GLB chassis (when `<Vehicle model-url=…>` is set); replaces the procedural body. */
  modelRoot: THREE.Group | null;
}

// NFS-inspired palette: metallic crimson body, carbon roof, gunmetal accents.
const BODY_COLOR = 0xcc2233;      // vibrant racing red
const BODY_SECONDARY = 0x991122;  // darker red for depth
const ROOF_COLOR = 0x1a1a1a;     // carbon black
const WHEEL_COLOR = 0x0a0a0a;     // gloss black tires
const WHEEL_HUB = 0xcccccc;       // silver rims
const HEADLIGHT_COLOR = 0xffffee; // warm white
const TAILLIGHT_COLOR = 0xff0000; // bright red
const GLASS_COLOR = 0x88ccff;     // tinted windshield
const CHROME_COLOR = 0xeeeeee;    // chrome trim/exhaust

/**
 * Builds a low-pogy arcade car (a body box + cabin + four wheels) for each
 * `Vehicle` entity and updates wheel rotation + body roll/pitch every frame
 * from the {@link Vehicle} juice state. This keeps the car readable from the
 * chase cam — it leans into turns, dives under braking, and the wheels spin.
 *
 * The visual group is a child of a roll-pivot so we can roll the chassis without
 * fighting the physics transform (which the transforms plugin owns).
 */
export const VehicleVisualSystem: System = defineSystem({
  name: 'VehicleVisualSystem',
  group: 'draw',

  update(state: State) {
    if (state.headless) return;
    const scene = getScene(state);
    if (!scene) return;
    const vehicles = visualQuery(state.world);

    for (const eid of vehicles) {
      let v = built.get(eid);
      if (!v) {
        v = buildChassis();
        built.set(eid, v);
        scene.add(v.group);
        const modelUrl = VehicleModelUrls.get(eid);
        if (modelUrl) {
          // GLB chassis: hide the procedural body and load the kart model.
          v.body.visible = false;
          for (const w of v.wheels) w.visible = false;
          loadModelChassis(v, modelUrl, VehicleModelYaw.get(eid) ?? 0);
        }
      }

      // Drive the group's position/orientation from the world transform.
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

      // Roll/pitch live on the inner pivot so the outer group stays physics-aligned.
      v.rollPivot.rotation.z = Vehicle.roll[eid];
      v.rollPivot.rotation.x = Vehicle.pitch[eid];

      // Spin the wheels (visual only; we don't simulate ground contact).
      const spin = Vehicle.wheelSpin[eid];
      for (const w of v.wheels) w.rotation.x = spin;
      spinModelWheels(v, spin);
    }
  },

  dispose() {
    for (const v of built.values()) {
      v.body.geometry.dispose();
      (v.body.material as THREE.Material).dispose();
      for (const w of v.wheels) {
        w.geometry.dispose();
        (w.material as THREE.Material).dispose();
      }
      v.modelRoot?.removeFromParent();
      v.group.parent?.remove(v.group);
    }
    built.clear();
  },
});

/**
 * Load a GLB chassis (from `model-url`) into the vehicle's roll pivot.
 * The model's base is snapped to the ground plane (assets with centre pivots
 * are shifted down by half their height); `modelYaw` rotates it (degrees) so
 * the nose faces +Z. GLB nodes named `wheel`/`tire`/`tyre`/`rim`
 * (case-insensitive) spin with the chassis wheelSpin.
 */
function loadModelChassis(v: ChassisVisual, url: string, modelYawDeg: number): void {
  createGLTFLoader()
    .loadAsync(url)
    .then((gltf) => {
      const root = gltf.scene;
      root.traverse((o) => {
        if ((o as THREE.Mesh).isMesh) {
          o.castShadow = true;
          o.receiveShadow = true;
        }
      });
      const box = new THREE.Box3().setFromObject(root);
      const sizeX = box.max.x - box.min.x;
      const sizeY = box.max.y - box.min.y;
      const sizeZ = box.max.z - box.min.z;

      // Some generated assets come out "standing up" (height ≫ length, e.g. a
      // kart exported Z-up). If the model is much taller than it is long, lay it
      // down: rotate X −90° so the Y span becomes the Z (length) span. Then the
      // kart's length runs along +Z like the physics forward expects.
      if (sizeY > sizeZ * 1.4 && sizeY > sizeX * 0.9) {
        root.rotation.x = -Math.PI / 2;
        // Re-measure after the rotation so the base-fit below is correct.
        root.updateMatrixWorld(true);
        const box2 = new THREE.Box3().setFromObject(root);
        if (Math.abs(box2.min.y) > 0.05 && box2.max.y - box2.min.y > 0) {
          root.position.y -= box2.min.y;
        }
      } else {
        if (Math.abs(box.min.y) > 0.05 && sizeY > 0) {
          root.position.y -= box.min.y;
        }
      }
      if (modelYawDeg !== 0) {
        root.rotation.y = THREE.MathUtils.degToRad(modelYawDeg);
      }
      v.modelRoot = root;
      v.rollPivot.add(root);
    })
    .catch((err) => {
      // Model failed to load — keep the procedural chassis visible.
      v.body.visible = true;
      for (const w of v.wheels) w.visible = true;
      console.warn(`[racing] vehicle model load failed: ${url}`, err);
    });
}

/** Spin GLB wheel nodes (named wheel/tire/tyre/rim) around their local X axis. */
function spinModelWheels(v: ChassisVisual, spin: number): void {
  if (!v.modelRoot) return;
  v.modelRoot.traverse((o) => {
    if (/(wheel|tire|tyre|rim)/.test(o.name.toLowerCase())) {
      o.rotation.x = spin;
    }
  });
}

function buildChassis(): ChassisVisual {
  const group = new THREE.Group();
  const rollPivot = new THREE.Group();
  group.add(rollPivot);

  // --- Materials (NFS-style) -----------------------------------------------
  const paintMat = new THREE.MeshStandardMaterial({
    color: BODY_COLOR,
    roughness: 0.18,
    metalness: 0.82,
    envMapIntensity: 1.4,
  });
  const darkPaintMat = new THREE.MeshStandardMaterial({
    color: BODY_SECONDARY,
    roughness: 0.25,
    metalness: 0.7,
  });
  const carbonMat = new THREE.MeshStandardMaterial({
    color: ROOF_COLOR,
    roughness: 0.32,
    metalness: 0.55,
  });
  const glassMat = new THREE.MeshStandardMaterial({
    color: GLASS_COLOR,
    roughness: 0.05,
    metalness: 0.9,
    transparent: true,
    opacity: 0.5,
  });
  const chromeMat = new THREE.MeshStandardMaterial({
    color: CHROME_COLOR,
    roughness: 0.08,
    metalness: 1.0,
  });
  const tailLightMat = new THREE.MeshStandardMaterial({
    color: TAILLIGHT_COLOR,
    emissive: TAILLIGHT_COLOR,
    emissiveIntensity: 0.6,
    roughness: 0.3,
  });
  const headLightMat = new THREE.MeshStandardMaterial({
    color: HEADLIGHT_COLOR,
    emissive: HEADLIGHT_COLOR,
    emissiveIntensity: 0.4,
    roughness: 0.2,
  });

  // --- Main Body (side profile extruded along X = width) --------------------
  // The shape is the car's SIDE PROFILE in the XY plane: x = length
  // (rear → nose), y = height (0 → roofline). We extrude along Z (width) then
  // rotate Y −90° so the finished body has: length→Z, height→Y, width→X.
  // (three.js is y-up: height must live on Y, never on X or Z.)
  const bodyShape = new THREE.Shape();
  bodyShape.moveTo(-1.05, 0.02);   // rear bottom
  bodyShape.lineTo(-1.0, 0.24);    // rear bumper
  bodyShape.lineTo(-0.88, 0.36);   // rear fender top
  bodyShape.lineTo(-0.55, 0.44);   // rear deck
  bodyShape.lineTo(-0.2, 0.46);    // roof peak
  bodyShape.lineTo(0.25, 0.44);    // windshield base
  bodyShape.lineTo(0.55, 0.36);    // hood
  bodyShape.lineTo(0.88, 0.28);    // front fender
  bodyShape.lineTo(1.02, 0.18);    // nose top
  bodyShape.lineTo(1.05, 0.06);    // nose tip
  bodyShape.lineTo(1.05, 0.02);    // nose bottom
  bodyShape.lineTo(-1.05, 0.02);   // close

  const extrudeSettings = {
    depth: 0.92,
    bevelEnabled: true,
    bevelThickness: 0.03,
    bevelSize: 0.03,
    bevelSegments: 2,
  };
  const bodyGeo = new THREE.ExtrudeGeometry(bodyShape, extrudeSettings);
  bodyGeo.center();
  // Rotate the extruded profile so height lands on Y and length on Z:
  // rotateY(-π/2) maps (x, y, z) → (-z, y, x).
  bodyGeo.rotateY(-Math.PI / 2);
  // After center() the height spans [-0.22, 0.22]; lift so the sills sit at
  // ~0.12 above the ground (between the wheel hubs).
  bodyGeo.translate(0, 0.34, 0);
  const body = new THREE.Mesh(bodyGeo, paintMat);
  body.castShadow = true;
  rollPivot.add(body);

  // --- Hood / Front clamshell (separate piece with vents) -------------------
  const hoodGeo = new THREE.BoxGeometry(0.92, 0.08, 0.85);
  const hood = new THREE.Mesh(hoodGeo, darkPaintMat);
  hood.position.set(0, 0.46, 0.65);
  hood.rotation.x = -0.06;
  hood.castShadow = true;
  rollPivot.add(hood);

  // Hood vents (two recessed lines)
  const ventGeo = new THREE.BoxGeometry(0.22, 0.02, 0.35);
  const ventMat = carbonMat;
  const ventL = new THREE.Mesh(ventGeo, ventMat);
  ventL.position.set(-0.22, 0.51, 0.62);
  const ventR = new THREE.Mesh(ventGeo, ventMat);
  ventR.position.set(0.22, 0.51, 0.62);
  rollPivot.add(ventL, ventR);

  // --- Cabin / Greenhouse --------------------------------------------------
  const cabinGeo = new THREE.BoxGeometry(0.78, 0.36, 0.95);
  const cabin = new THREE.Mesh(cabinGeo, carbonMat);
  cabin.position.set(0, 0.72, -0.12);
  cabin.castShadow = true;
  rollPivot.add(cabin);

  // Windshield (angled)
  const windshieldGeo = new THREE.PlaneGeometry(0.72, 0.34);
  const windshield = new THREE.Mesh(windshieldGeo, glassMat);
  windshield.position.set(0, 0.70, 0.30);
  windshield.rotation.x = -0.45;
  rollPivot.add(windshield);

  // Rear window
  const rearWindowGeo = new THREE.PlaneGeometry(0.68, 0.26);
  const rearWindow = new THREE.Mesh(rearWindowGeo, glassMat);
  rearWindow.position.set(0, 0.68, -0.55);
  rearWindow.rotation.x = 0.55;
  rollPivot.add(rearWindow);

  // --- Side Skirts & Rocker Panels -----------------------------------------
  const skirtGeo = new THREE.BoxGeometry(0.04, 0.14, 1.7);
  const skirtL = new THREE.Mesh(skirtGeo, darkPaintMat);
  skirtL.position.set(-0.52, 0.24, 0.0);
  const skirtR = new THREE.Mesh(skirtGeo, darkPaintMat);
  skirtR.position.set(0.52, 0.24, 0.0);
  rollPivot.add(skirtL, skirtR);

  // --- Headlights (angular, aggressive) ------------------------------------
  const hlGeo = new THREE.BoxGeometry(0.18, 0.08, 0.04);
  const hlL = new THREE.Mesh(hlGeo, headLightMat);
  hlL.position.set(-0.28, 0.24, 1.05);
  const hlR = new THREE.Mesh(hlGeo, headLightMat);
  hlR.position.set(0.28, 0.24, 1.05);
  rollPivot.add(hlL, hlR);

  // Headlight housings (chrome surround)
  const hlHousingGeo = new THREE.BoxGeometry(0.20, 0.10, 0.03);
  const hlHousingMat = chromeMat;
  const hhlL = new THREE.Mesh(hlHousingGeo, hlHousingMat);
  hhlL.position.set(-0.28, 0.23, 1.07);
  const hhlR = new THREE.Mesh(hlHousingGeo, hlHousingMat);
  hhlR.position.set(0.28, 0.23, 1.07);
  rollPivot.add(hhlL, hhlR);

  // --- Taillights (thin LED strip across rear) ----------------------------
  const tlStripGeo = new THREE.BoxGeometry(0.96, 0.03, 0.02);
  const tlStrip = new THREE.Mesh(tlStripGeo, tailLightMat);
  tlStrip.position.set(0, 0.38, -1.01);
  rollPivot.add(tlStrip);

  // --- Grille / Lower intake ----------------------------------------------
  const grilleGeo = new THREE.BoxGeometry(0.6, 0.12, 0.03);
  const grilleMat = new THREE.MeshStandardMaterial({ color: 0x111111, roughness: 0.7 });
  const grille = new THREE.Mesh(grilleGeo, grilleMat);
  grille.position.set(0, 0.22, 1.03);
  rollPivot.add(grille);

  // --- Spoiler (large racing wing) ----------------------------------------
  const wingGeo = new THREE.BoxGeometry(1.12, 0.04, 0.28);
  const wing = new THREE.Mesh(wingGeo, carbonMat);
  wing.position.set(0, 0.92, -0.92);
  rollPivot.add(wing);

  // Spoiler stands
  const standGeo = new THREE.BoxGeometry(0.05, 0.28, 0.06);
  const standL = new THREE.Mesh(standGeo, carbonMat);
  standL.position.set(-0.42, 0.78, -0.92);
  const standR = new THREE.Mesh(standGeo, carbonMat);
  standR.position.set(0.42, 0.78, -0.92);
  rollPivot.add(standL, standR);

  // --- Side Mirrors -------------------------------------------------------
  const mirrorGeo = new THREE.BoxGeometry(0.06, 0.06, 0.09);
  const mirrorL = new THREE.Mesh(mirrorGeo, darkPaintMat);
  mirrorL.position.set(-0.56, 0.58, 0.20);
  const mirrorR = new THREE.Mesh(mirrorGeo, darkPaintMat);
  mirrorR.position.set(0.56, 0.58, 0.20);
  rollPivot.add(mirrorL, mirrorR);

  // --- Exhaust Pipes (dual chrome) ----------------------------------------
  const exhaustGeo = new THREE.CylinderGeometry(0.035, 0.04, 0.18, 8);
  exhaustGeo.rotateZ(Math.PI / 2);
  const exhaustL = new THREE.Mesh(exhaustGeo, chromeMat);
  exhaustL.position.set(-0.18, 0.22, -1.08);
  const exhaustR = new THREE.Mesh(exhaustGeo, chromeMat);
  exhaustR.position.set(0.18, 0.22, -1.08);
  rollPivot.add(exhaustL, exhaustR);

  // --- Wheels (low-profile performance tires + multi-spoke rims) ----------
  const wheels: THREE.Mesh[] = [];
  const wheelGeo = new THREE.CylinderGeometry(0.33, 0.33, 0.26, 20);
  wheelGeo.rotateZ(Math.PI / 2);
  const tireMat = new THREE.MeshStandardMaterial({
    color: WHEEL_COLOR,
    roughness: 0.92,
    metalness: 0.02,
  });

  // Rim: 5-spoke star pattern using multiple boxes
  function buildRim(): THREE.Group {
    const rimGroup = new THREE.Group();
    const hubGeo = new THREE.CylinderGeometry(0.14, 0.14, 0.27, 12);
    hubGeo.rotateZ(Math.PI / 2);
    const hub = new THREE.Mesh(hubGeo, new THREE.MeshStandardMaterial({
      color: WHEEL_HUB, roughness: 0.22, metalness: 0.88,
    }));
    rimGroup.add(hub);

    // 5 spokes — the tire cylinder's axis is X (after rotateZ), so the wheel
    // face is the YZ plane: place spokes radially in YZ (rotation.x).
    const spokeGeo = new THREE.BoxGeometry(0.05, 0.26, 0.16);
    const spokeMat = new THREE.MeshStandardMaterial({
      color: WHEEL_HUB, roughness: 0.18, metalness: 0.92,
    });
    for (let i = 0; i < 5; i++) {
      const spoke = new THREE.Mesh(spokeGeo, spokeMat);
      const angle = (i / 5) * Math.PI * 2;
      spoke.position.set(0, Math.cos(angle) * 0.17, Math.sin(angle) * 0.17);
      spoke.rotation.x = angle;
      rimGroup.add(spoke);
    }
    return rimGroup;
  }

  const offsets: [number, number, number][] = [
    [-0.54, 0.33, 0.68],
    [0.54, 0.33, 0.68],
    [-0.54, 0.33, -0.68],
    [0.54, 0.33, -0.68],
  ];
  for (const [x, y, z] of offsets) {
    const wheel = new THREE.Mesh(wheelGeo, tireMat);
    wheel.position.set(x, y, z);
    wheel.castShadow = true;

    const rim = buildRim();
    rim.position.copy(wheel.position);

    rollPivot.add(wheel);
    rollPivot.add(rim);
    wheels.push(wheel);
  }

  // --- Ground Effect / Front Splitter -------------------------------------
  const splitterGeo = new THREE.BoxGeometry(0.96, 0.04, 0.18);
  const splitter = new THREE.Mesh(splitterGeo, carbonMat);
  splitter.position.set(0, 0.14, 1.0);
  rollPivot.add(splitter);

  // Rear diffuser
  const diffuserGeo = new THREE.BoxGeometry(0.84, 0.06, 0.16);
  const diffuser = new THREE.Mesh(diffuserGeo, carbonMat);
  diffuser.position.set(0, 0.13, -1.0);
  rollPivot.add(diffuser);

  // --- Environment Map (night city reflections) ----------------------------
  const envMapSize = 256;
  const envCanvas = document.createElement('canvas');
  envCanvas.width = envMapSize;
  envCanvas.height = envMapSize;
  const envCtx = envCanvas.getContext('2d')!;
  // Dark sky gradient.
  const skyGrad = envCtx.createLinearGradient(0, 0, 0, envMapSize);
  skyGrad.addColorStop(0, '#050510');   // top (space)
  skyGrad.addColorStop(0.6, '#0a0a1a');  // mid
  skyGrad.addColorStop(1, '#1a0a15');   // horizon (city glow)
  envCtx.fillStyle = skyGrad;
  envCtx.fillRect(0, 0, envMapSize, envMapSize);
  // Add bright spots for "city lights" reflection.
  for (let i = 0; i < 40; i++) {
    const x = Math.random() * envMapSize;
    const y = envMapSize * 0.7 + Math.random() * envMapSize * 0.3;
    const r = 1 + Math.random() * 3;
    const grad = envCtx.createRadialGradient(x, y, 0, x, y, r);
    const hue = Math.random() > 0.5 ? 'rgba(255,150,50,' : 'rgba(100,180,255,';
    grad.addColorStop(0, hue + '0.8)');
    grad.addColorStop(1, hue + '0)');
    envCtx.fillStyle = grad;
    envCtx.beginPath();
    envCtx.arc(x, y, r, 0, Math.PI * 2);
    envCtx.fill();
  }
  const envTexture = new THREE.CanvasTexture(envCanvas);
  envTexture.mapping = THREE.EquirectangularReflectionMapping;

  // Apply envMap to all glossy/metallic materials on the car.
  paintMat.envMap = envTexture;
  darkPaintMat.envMap = envTexture;
  // chromeMat is defined later in the function — apply via traverse after return if needed.
  // For now, the main body paint has the reflection which is the most visible part.

  return { group, body, wheels, rollPivot, modelRoot: null };
}
