/**
 * Track dressing: places the generated prop GLBs around the circuit.
 *
 * Two rules the previous version broke, and which matter more than the prop
 * choice itself:
 *
 * 1. **Every model is fitted to a declared real-world size.** Assets from the
 *    GameAssets/Hunyuan3D pipeline come out at arbitrary scale — dropping one
 *    in untouched is how a road sign ended up bigger than the grandstand.
 * 2. **Nothing is placed on the racing surface.** Offsets are measured from the
 *    barrier line (`width/2 + shoulder`) outwards, so scenery never spawns
 *    inside the track or inside the wall it is supposed to sit behind.
 *
 * Solid props (tyre stacks, boulders) additionally register a collision circle
 * with the racing plugin, so hitting one costs you the corner.
 */
import * as THREE from 'three';
import * as GAME from 'vibegame';

const ASSETS = '/assets/meshes/props';

interface PropDef {
  /** File stem under `/assets/meshes/props` (LOD0 is used). */
  file: string;
  /** Target height of the model (m) — everything scales from this. */
  height: number;
  /** Distance outside the barrier line (m). */
  offset: number;
  /** Metres of track between two of this prop. */
  spacing: number;
  /** Place on the left (-1), right (+1) or both (0) sides. */
  side: -1 | 0 | 1;
  /** Random yaw spread (radians). */
  yawJitter?: number;
  /** Random size spread (fraction). */
  sizeJitter?: number;
  /** Register a collision circle of this radius (m). */
  solid?: number;
}

/** Props per circuit section (see `src/track.ts` for the section layout). */
const SECTION_PROPS: Record<string, PropDef[]> = {
  main: [
    { file: 'barrier_red', height: 1.0, offset: 0.6, spacing: 9, side: 0 },
    { file: 'road_sign', height: 2.4, offset: 5, spacing: 120, side: 1 },
  ],
  turn1: [
    {
      file: 'tire_stack',
      height: 0.9,
      offset: 1.2,
      spacing: 7,
      side: 1,
      solid: 1.0,
    },
    { file: 'guard_rail', height: 0.9, offset: 0.8, spacing: 12, side: -1 },
    {
      file: 'palm_tree',
      height: 8.5,
      offset: 14,
      spacing: 26,
      side: 1,
      yawJitter: Math.PI,
      sizeJitter: 0.25,
    },
  ],
  climb: [
    {
      file: 'pine_tree',
      height: 9.5,
      offset: 10,
      spacing: 17,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
    { file: 'guard_rail', height: 0.9, offset: 0.8, spacing: 12, side: 1 },
    {
      file: 'rock_boulder',
      height: 1.7,
      offset: 22,
      spacing: 45,
      side: -1,
      yawJitter: Math.PI,
      sizeJitter: 0.4,
    },
  ],
  crest: [
    { file: 'checkered_flag', height: 2.2, offset: 3, spacing: 22, side: 0 },
    {
      file: 'pine_tree',
      height: 9.5,
      offset: 13,
      spacing: 24,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
  ],
  descent: [
    {
      file: 'tire_stack',
      height: 0.9,
      offset: 1.2,
      spacing: 8,
      side: -1,
      solid: 1.0,
    },
    {
      file: 'pine_tree',
      height: 9.5,
      offset: 12,
      spacing: 20,
      side: 1,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
    { file: 'road_sign', height: 2.4, offset: 5, spacing: 90, side: -1 },
  ],
  esses: [
    { file: 'barrier_red', height: 1.0, offset: 0.6, spacing: 8, side: 0 },
    {
      file: 'rock_boulder',
      height: 1.7,
      offset: 16,
      spacing: 32,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.4,
    },
  ],
  hairpin: [
    {
      file: 'tire_stack',
      height: 0.9,
      offset: 1.0,
      spacing: 5,
      side: 0,
      solid: 1.0,
    },
    { file: 'road_sign', height: 2.4, offset: 6, spacing: 60, side: 1 },
  ],
  infield: [
    { file: 'guard_rail', height: 0.9, offset: 0.8, spacing: 12, side: 0 },
    {
      file: 'palm_tree',
      height: 8.5,
      offset: 15,
      spacing: 22,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.25,
    },
  ],
  final: [
    { file: 'barrier_red', height: 1.0, offset: 0.6, spacing: 9, side: 0 },
    {
      file: 'palm_tree',
      height: 8.5,
      offset: 16,
      spacing: 26,
      side: -1,
      yawJitter: Math.PI,
      sizeJitter: 0.25,
    },
  ],
};

const DEFAULT_PROPS: PropDef[] = [
  { file: 'barrier_red', height: 1.0, offset: 0.6, spacing: 12, side: 0 },
];

/** Cache of loaded prototypes, so each GLB is fetched and parsed once. */
const prototypes = new Map<string, Promise<THREE.Object3D | null>>();

function loadPrototype(url: string): Promise<THREE.Object3D | null> {
  let p = prototypes.get(url);
  if (!p) {
    p = GAME.createGLTFLoader()
      .loadAsync(url)
      .then((gltf) => gltf.scene as THREE.Object3D)
      .catch((err) => {
        console.warn('[track-props] failed to load', url, err);
        return null;
      });
    prototypes.set(url, p);
  }
  return p;
}

/**
 * Wrap a normalised model in a group so callers can move it freely.
 *
 * `fitModel` does the real work: it stands the model up, measures its heading
 * from the vertex cloud (not the bounding box) and scales it to a real-world
 * size. `align: 'across'` is for the start gantry, whose long side has to span
 * the road rather than point down it.
 */
function instantiate(
  prototype: THREE.Object3D,
  size: number,
  fit: 'height' | 'width' = 'height',
  align: 'forward' | 'across' = 'forward'
): THREE.Group {
  const holder = new THREE.Group();
  const clone = prototype.clone(true);
  holder.add(clone);
  GAME.fitModel(clone, { align, fit, size, ground: true });
  clone.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh) {
      mesh.castShadow = true;
      mesh.receiveShadow = true;
    }
  });
  return holder;
}

let dressed = false;

/** Metres between light poles along the circuit. */
const POLE_SPACING = 34;
/** Metres between poles that carry an actual PointLight (the renderer keeps
 *  the 12 nearest lit, so spare ones are just emissive decoration). */
const POLE_LIGHT_EVERY = 2;
/** Warm lamp colour shared by the emissive lamp and its point light. */
const LAMP_COLOR = 0xffb45e;

/**
 * One light pole: a dark pillar with an arm, an emissive lamp housing and a
 * soft glow sprite. Returns the group and the lamp world position (for the
 * point light to sit at).
 */
function buildLightPole(): { group: THREE.Group; lamp: THREE.Vector3 } {
  const group = new THREE.Group();
  const poleMat = new THREE.MeshStandardMaterial({
    color: 0x2a2e3a,
    roughness: 0.55,
    metalness: 0.6,
  });
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, 4.6, 8), poleMat);
  pole.position.y = 2.3;
  group.add(pole);

  const arm = new THREE.Mesh(new THREE.BoxGeometry(1.5, 0.09, 0.09), poleMat);
  arm.position.set(0.75, 4.35, 0);
  group.add(arm);

  const lampMat = new THREE.MeshStandardMaterial({
    color: LAMP_COLOR,
    emissive: LAMP_COLOR,
    emissiveIntensity: 3.5,
    roughness: 0.3,
  });
  const lamp = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.16, 0.24), lampMat);
  lamp.position.set(1.4, 4.3, 0);
  group.add(lamp);

  // Soft halo so the bloom has something to pick up even from a distance.
  const glowMat = new THREE.MeshBasicMaterial({
    color: LAMP_COLOR,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
  });
  const glow = new THREE.Mesh(new THREE.SphereGeometry(0.55, 10, 8), glowMat);
  glow.position.copy(lamp.position);
  group.add(glow);

  group.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh) mesh.castShadow = true;
  });

  return { group, lamp: lamp.position.clone() };
}

/**
 * Walks the circuit once on the first frame and scatters section-appropriate
 * scenery. Runs off the spline, so it always agrees with the road that was
 * actually built.
 */
export const TrackPropSpawnSystem: GAME.System = {
  name: 'TrackPropSpawnSystem',
  group: 'simulation',
  update(state: GAME.State) {
    if (state.headless || dressed) return;
    const trackEid = GAME.getPrimaryTrackEntity();
    if (trackEid === undefined) return;
    const spline = GAME.getTrackSpline(trackEid);
    const scene = GAME.getScene(state);
    if (!spline || !scene) return;
    // The prop GLBs carry KTX2 textures — wire the transcoder from the live
    // renderer before the first load or every one of them fails to parse.
    GAME.ensureKTX2LoaderReady(state);
    dressed = true;
    void dressTrack(
      scene as THREE.Object3D,
      spline,
      GAME.RaceTrackComponent.shoulder[trackEid] || 3,
      state
    );
  },
};

async function dressTrack(
  scene: THREE.Object3D,
  spline: GAME.TrackSpline,
  shoulder: number,
  state: GAME.State
): Promise<void> {
  const group = new THREE.Group();
  group.name = 'TrackDressing';
  scene.add(group);

  // Light poles: alternate sides, every POLE_SPACING metres. Every other pole
  // carries a real PointLight entity (the renderer lights the 12 nearest).
  const lightGroup = new THREE.Group();
  lightGroup.name = 'TrackLampLights';
  scene.add(lightGroup);
  let poleIndex = 0;
  for (let s = 40; s < spline.length - 40; s += POLE_SPACING) {
    const frame = spline.sampleAt(s);
    const side = poleIndex % 2 === 0 ? -1 : 1;
    poleIndex++;
    const lateral = (frame.width * 0.5 + shoulder + 0.6) * side;
    const { group: pole, lamp } = buildLightPole();
    pole.position.set(
      frame.x + frame.rx * lateral,
      frame.y + frame.ry * lateral - 0.05,
      frame.z + frame.rz * lateral
    );
    // Face the pole arm toward the track.
    pole.rotation.y = Math.atan2(frame.tx, frame.tz) + (side > 0 ? Math.PI : 0);
    pole.updateMatrixWorld(true);
    group.add(pole);

    if (poleIndex % POLE_LIGHT_EVERY === 0) {
      const eid = state.createEntity();
      state.addComponent(eid, GAME.PointLight);
      state.addComponent(eid, GAME.Transform);
      GAME.PointLight.color[eid] = LAMP_COLOR;
      GAME.PointLight.intensity[eid] = 22;
      GAME.PointLight.distance[eid] = 26;
      GAME.PointLight.decay[eid] = 2;
      // Lamp world position = pole world position + local arm offset rotated
      // by the pole yaw.
      const lampWorld = lamp.clone().applyMatrix4(pole.matrixWorld);
      GAME.Transform.posX[eid] = lampWorld.x;
      GAME.Transform.posY[eid] = lampWorld.y;
      GAME.Transform.posZ[eid] = lampWorld.z;
      GAME.Transform.dirty[eid] = 1;
    }
  }

  // Start/finish gantry, sized to span the road.
  const startFrame = spline.sampleAt(0);
  const banner = await loadPrototype(`${ASSETS}/start_banner_lod0.glb`);
  if (banner) {
    const holder = instantiate(banner, startFrame.width + 8, 'width', 'across');
    holder.position.set(startFrame.x, startFrame.y, startFrame.z);
    // The gantry's long side is on +X now, so aligning its local +Z with the
    // track tangent makes it span the road.
    holder.rotation.y = Math.atan2(startFrame.tx, startFrame.tz);
    group.add(holder);
  }

  // Everything else: walk the circuit and place per-section props on cadence.
  const cadence = new Map<string, number>();
  const step = 4;
  for (let s = 0; s < spline.length; s += step) {
    const frame = spline.sampleAt(s);
    const defs = SECTION_PROPS[frame.section] ?? DEFAULT_PROPS;
    const barrier = frame.width * 0.5 + shoulder;

    for (const def of defs) {
      const key = `${def.file}:${def.offset}:${def.side}`;
      const acc = (cadence.get(key) ?? def.spacing) + step;
      if (acc < def.spacing) {
        cadence.set(key, acc);
        continue;
      }
      cadence.set(key, 0);

      const sides: number[] = def.side === 0 ? [-1, 1] : [def.side];
      const prototype = await loadPrototype(`${ASSETS}/${def.file}_lod0.glb`);
      if (!prototype) continue;

      for (const side of sides) {
        const size =
          def.height * (1 + (def.sizeJitter ?? 0) * (Math.random() - 0.5) * 2);
        const holder = instantiate(prototype, size);
        const lateral = (barrier + def.offset + Math.random() * 1.5) * side;
        holder.position.set(
          frame.x + frame.rx * lateral,
          frame.y + frame.ry * lateral - 0.05,
          frame.z + frame.rz * lateral
        );
        holder.rotation.y =
          Math.atan2(frame.tx, frame.tz) +
          (def.yawJitter ? (Math.random() - 0.5) * def.yawJitter : 0);
        group.add(holder);

        if (def.solid) {
          GAME.addTrackObstacle(
            holder.position.x,
            holder.position.z,
            def.solid,
            0.5
          );
        }
      }
    }
  }
}

/** Reset the one-shot guard (used when the page hot-reloads the module). */
export function resetTrackDressing(): void {
  dressed = false;
  prototypes.clear();
  GAME.clearTrackObstacles();
}
