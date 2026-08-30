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
 * Solid props (barrels, log piles) additionally register a collision circle
 * with the racing plugin, so hitting one costs you the corner.
 */
import * as THREE from 'three';
import * as GAME from 'aigamekit-vibegame';

/** Pack folder under `/assets/meshes/` (Crystal Vale shared GLBs). */
type MeshPack = 'village' | 'forest' | 'infra' | 'props';

interface PropDef {
  /** Folder under `/assets/meshes/`. */
  pack: MeshPack;
  /** File stem (LOD0 is used). */
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

/**
 * Props per circuit section (see `src/track.ts` for the section layout).
 *
 * Two rules the corridor imposes, both easy to get wrong:
 *
 *  1. `offset` is measured from the **barrier** (`width/2 + shoulder`). The
 *     carved shelf is run-off + berm + talude (`flatten-falloff` on the bed
 *     Road, ~16 m). Offset 10 sits in the gravel; trees belong on the talude
 *     (14–18 m), not past it — past the cut they plant on the cliff lip and
 *     read as floating above the track.
 *  2. Props are grounded on the terrain, not on the deck. On the flyover the
 *     terrain is ~20 m below the road, so anything placed there ends up in the
 *     valley — those sections get barriers only, or nothing at all.
 */
const SECTION_PROPS: Record<string, PropDef[]> = {
  // Downtown: crates off the corridor, torches on the approach to Turn 1.
  city: [
    {
      pack: 'village',
      file: 'wooden_crate',
      height: 0.8,
      offset: 20,
      spacing: 18,
      side: 0,
    },
    {
      pack: 'village',
      file: 'torch_post',
      height: 2.2,
      offset: 22,
      spacing: 120,
      side: 1,
    },
  ],
  turn1: [
    {
      pack: 'village',
      file: 'wooden_barrel',
      height: 0.9,
      offset: 1.2,
      spacing: 7,
      side: 1,
      solid: 1.0,
    },
    {
      pack: 'village',
      file: 'log_pile',
      height: 0.8,
      offset: 20,
      spacing: 16,
      side: -1,
    },
    {
      pack: 'forest',
      file: 'tree_oak',
      height: 8.5,
      offset: 15,
      spacing: 26,
      side: 1,
      yawJitter: Math.PI,
      sizeJitter: 0.25,
    },
  ],
  // The rim: last solid ground before the drop — barrels off the outside.
  rim: [
    {
      pack: 'village',
      file: 'wooden_barrel',
      height: 0.9,
      offset: 20,
      spacing: 14,
      side: 0,
    },
    {
      pack: 'village',
      file: 'notice_board',
      height: 2.0,
      offset: 22,
      spacing: 70,
      side: -1,
    },
  ],
  // On the span: nothing on the ground (it is 20 m down there). The deck's own
  // barriers are part of the track mesh.
  flyover: [],
  landing: [
    {
      pack: 'village',
      file: 'wooden_crate',
      height: 0.8,
      offset: 20,
      spacing: 14,
      side: 0,
    },
  ],
  west: [
    {
      pack: 'village',
      file: 'log_pile',
      height: 0.8,
      offset: 20,
      spacing: 18,
      side: 0,
    },
    {
      pack: 'forest',
      file: 'tree_pine',
      height: 9.5,
      offset: 16,
      spacing: 22,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
  ],
  climb: [
    {
      pack: 'forest',
      file: 'pine_dark',
      height: 9.5,
      offset: 15,
      spacing: 17,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
    {
      pack: 'village',
      file: 'wooden_crate',
      height: 0.8,
      offset: 20,
      spacing: 16,
      side: 1,
    },
    {
      pack: 'props',
      file: 'rock_mossy',
      height: 1.7,
      offset: 28,
      spacing: 45,
      side: -1,
      yawJitter: Math.PI,
      sizeJitter: 0.4,
    },
  ],
  crest: [
    {
      pack: 'village',
      file: 'iron_brazier',
      height: 1.1,
      offset: 20,
      spacing: 22,
      side: 0,
    },
    {
      pack: 'forest',
      file: 'tree_pine',
      height: 9.5,
      offset: 16,
      spacing: 24,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
  ],
  hairpin: [
    {
      pack: 'village',
      file: 'log_pile',
      height: 0.8,
      offset: 1.0,
      spacing: 5,
      side: 0,
      solid: 1.0,
    },
    {
      pack: 'village',
      file: 'torch_post',
      height: 2.2,
      offset: 20,
      spacing: 60,
      side: 1,
    },
  ],
  descent: [
    {
      pack: 'village',
      file: 'wooden_barrel',
      height: 0.9,
      offset: 1.2,
      spacing: 8,
      side: -1,
      solid: 1.0,
    },
    {
      pack: 'forest',
      file: 'tree_oak',
      height: 9.5,
      offset: 15,
      spacing: 20,
      side: 1,
      yawJitter: Math.PI,
      sizeJitter: 0.3,
    },
    {
      pack: 'village',
      file: 'notice_board',
      height: 2.0,
      offset: 20,
      spacing: 90,
      side: -1,
    },
  ],
  // Under the flyover: keep it clear so the span reads from the cockpit.
  underpass: [
    {
      pack: 'village',
      file: 'wooden_crate',
      height: 0.8,
      offset: 20,
      spacing: 14,
      side: 0,
    },
  ],
  climbout: [
    {
      pack: 'village',
      file: 'log_pile',
      height: 0.8,
      offset: 20,
      spacing: 16,
      side: 0,
    },
    {
      pack: 'forest',
      file: 'tree_oak',
      height: 8.5,
      offset: 16,
      spacing: 24,
      side: 0,
      yawJitter: Math.PI,
      sizeJitter: 0.25,
    },
  ],
  return: [
    {
      pack: 'village',
      file: 'wooden_barrel',
      height: 0.9,
      offset: 20,
      spacing: 14,
      side: 0,
    },
    {
      pack: 'forest',
      file: 'pine_dark',
      height: 8.5,
      offset: 18,
      spacing: 30,
      side: -1,
      yawJitter: Math.PI,
      sizeJitter: 0.25,
    },
  ],
};

const DEFAULT_PROPS: PropDef[] = [
  {
    pack: 'village',
    file: 'wooden_crate',
    height: 0.8,
    offset: 20,
    spacing: 16,
    side: 0,
  },
];

/**
 * How each GLB has to be transformed to stand at a real-world size.
 *
 * Props are *instanced*, so the model itself is never cloned per placement:
 * every barrel along 5 km of circuit shares one `InstancedMesh2` through the
 * engine's pool. What each placement needs is therefore not an Object3D but
 * three numbers — scale, the yaw `fitModel` used to point the model down +Z,
 * and how far its feet sit off the origin.
 *
 * Measuring costs one throwaway clone per GLB (not per prop), which is the
 * whole point: the old code cloned the scene 1500 times and handed three.js
 * 1500 objects to cull, sort and draw every frame.
 */
interface PropFit {
  url: string;
  lod1Url: string;
  lod2Url: string;
  scale: number;
  yaw: number;
  groundOffset: number;
  /** Target height used for viaduct crown culling (m). */
  height: number;
}

const fits = new Map<string, Promise<PropFit | null>>();

function meshUrl(
  pack: MeshPack,
  file: string,
  lod: 'lod0' | 'lod1' | 'lod2'
): string {
  return `/assets/meshes/${pack}/${file}_${lod}.glb`;
}

function measureProp(
  pack: MeshPack,
  file: string,
  size: number,
  fit: 'height' | 'width' = 'height',
  align: 'forward' | 'across' = 'forward'
): Promise<PropFit | null> {
  const key = `${pack}/${file}:${size}:${fit}:${align}:stand-never`;
  let p = fits.get(key);
  if (!p) {
    p = GAME.createGLTFLoader()
      .loadAsync(meshUrl(pack, file, 'lod0'))
      .then((gltf) => {
        const probe = (gltf.scene as THREE.Object3D).clone(true);
        GAME.fitModel(probe, {
          align,
          fit,
          size,
          ground: true,
          // Crystal Vale GLBs are already Y-up, origin at the feet. The
          // vehicle Y↔Z stand-up would lay a tree on its side, scale its
          // *width* to `height`, and `placeProp` would add that as Y.
          standUp: 'never',
        });
        probe.updateMatrixWorld(true);
        return {
          url: meshUrl(pack, file, 'lod0'),
          lod1Url: meshUrl(pack, file, 'lod1'),
          lod2Url: meshUrl(pack, file, 'lod2'),
          scale: probe.scale.x,
          yaw: probe.rotation.y,
          groundOffset: probe.position.y,
          height: size,
        } satisfies PropFit;
      })
      .catch((err) => {
        console.warn('[track-props] failed to load', pack, file, err);
        // Do NOT cache the failure: a transient network blip would otherwise
        // blacklist this prop for the whole session (and every re-dress).
        fits.delete(key);
        return null;
      });
    fits.set(key, p);
  }
  return p;
}

/** Place one instance of a measured prop through the engine's pool. */
function placeProp(
  state: GAME.State,
  fit: PropFit,
  x: number,
  y: number,
  z: number,
  yaw: number,
  sizeScale = 1
): void {
  // One surface sample for plant + TerrainSpawned. Mixing sampleTerrainHeight
  // (max of a footprint) with sampleTerrainSurface baked a positive yOffset on
  // the talude and the resync kept trees hanging above the cut.
  const surface = GAME.sampleTerrainSurface(state, x, z, 0.75);
  const surfaceY = surface?.worldY ?? y;
  const foot = fit.groundOffset * sizeScale;
  const plantY = surfaceY + foot;
  if (GAME.crownHitsFlyingDeck(state, x, z, plantY + fit.height * sizeScale)) {
    return;
  }
  const eid = GAME.spawnInstancedGltf(state, {
    url: fit.url,
    lod1Url: fit.lod1Url,
    lod2Url: fit.lod2Url,
    x,
    y: plantY,
    z,
    yaw: yaw + fit.yaw,
    scale: fit.scale * sizeScale,
    // Roadside furniture past this distance is a couple of pixels; the LOD
    // chain already thins it out, culling drops it entirely.
    cullDistance: 320,
  });
  state.addComponent(eid, GAME.TerrainSpawned);
  GAME.TerrainSpawned.yOffset[eid] = foot;
  GAME.TerrainSpawned.surfaceEpsilon[eid] = 0.75;
}

let dressed = false;

/** Metres between light poles along the circuit. */
const POLE_SPACING = 34;
/** Metres between poles that carry an actual PointLight (the renderer keeps
 *  the 12 nearest lit, so spare ones are just emissive decoration). */
const POLE_LIGHT_EVERY = 2;
/** Warm lamp colour shared by the emissive lamp and its point light. */
const LAMP_COLOR = 0xffb45e;
/** Lamp housing offset from the pole base, in pole-local space. */
const LAMP_OFFSET = new THREE.Vector3(1.4, 4.3, 0);

/**
 * The light poles, as four `InstancedMesh` batches instead of 150 groups.
 *
 * A 5 km circuit takes ~160 poles; as separate meshes that is 640 objects
 * three.js has to walk, cull, sort and draw every frame for scenery nobody
 * looks at. The parts are identical, so one batch per part draws the whole
 * circuit in four calls. `frustumCulled` stays off: the batch spans the map,
 * so its bounding sphere is always on screen anyway and computing it per frame
 * is wasted work.
 */
interface PoleBatch {
  group: THREE.Group;
  add(x: number, y: number, z: number, yaw: number): void;
  /** Lamp position in world space for the last added pole. */
  lampAt(x: number, y: number, z: number, yaw: number): THREE.Vector3;
  finish(): void;
}

function createPoleBatch(count: number): PoleBatch {
  const group = new THREE.Group();
  group.name = 'TrackLampPoles';

  const poleMat = new THREE.MeshStandardMaterial({
    color: 0x2a2e3a,
    roughness: 0.55,
    metalness: 0.6,
  });
  const lampMat = new THREE.MeshStandardMaterial({
    color: LAMP_COLOR,
    emissive: LAMP_COLOR,
    emissiveIntensity: 3.5,
    roughness: 0.3,
  });
  const glowMat = new THREE.MeshBasicMaterial({
    color: LAMP_COLOR,
    transparent: true,
    opacity: 0.35,
    depthWrite: false,
  });

  const parts: {
    mesh: THREE.InstancedMesh;
    offset: THREE.Vector3;
  }[] = [
    {
      mesh: new THREE.InstancedMesh(
        new THREE.CylinderGeometry(0.09, 0.12, 4.6, 8),
        poleMat,
        count
      ),
      offset: new THREE.Vector3(0, 2.3, 0),
    },
    {
      mesh: new THREE.InstancedMesh(
        new THREE.BoxGeometry(1.5, 0.09, 0.09),
        poleMat,
        count
      ),
      offset: new THREE.Vector3(0.75, 4.35, 0),
    },
    {
      mesh: new THREE.InstancedMesh(
        new THREE.BoxGeometry(0.5, 0.16, 0.24),
        lampMat,
        count
      ),
      offset: LAMP_OFFSET.clone(),
    },
    {
      mesh: new THREE.InstancedMesh(
        new THREE.SphereGeometry(0.55, 10, 8),
        glowMat,
        count
      ),
      offset: LAMP_OFFSET.clone(),
    },
  ];
  for (const part of parts) {
    part.mesh.castShadow = true;
    part.mesh.frustumCulled = false;
    part.mesh.count = 0;
    group.add(part.mesh);
  }

  const m = new THREE.Matrix4();
  const q = new THREE.Quaternion();
  const pos = new THREE.Vector3();
  const one = new THREE.Vector3(1, 1, 1);
  let n = 0;

  const worldOffset = (
    x: number,
    y: number,
    z: number,
    yaw: number,
    offset: THREE.Vector3
  ): THREE.Vector3 =>
    offset
      .clone()
      .applyAxisAngle(new THREE.Vector3(0, 1, 0), yaw)
      .add(new THREE.Vector3(x, y, z));

  return {
    group,
    add(x, y, z, yaw) {
      if (n >= count) return;
      q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), yaw);
      for (const part of parts) {
        pos.copy(worldOffset(x, y, z, yaw, part.offset));
        m.compose(pos, q, one);
        part.mesh.setMatrixAt(n, m);
      }
      n++;
    },
    lampAt: (x, y, z, yaw) => worldOffset(x, y, z, yaw, LAMP_OFFSET),
    finish() {
      for (const part of parts) {
        part.mesh.count = n;
        part.mesh.instanceMatrix.needsUpdate = true;
        part.mesh.computeBoundingSphere();
      }
    },
  };
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
    // The <Road flatten> carve runs in setup; dressing on the first simulation
    // frame used to sample the uncarved heightfield and leave oaks/pines
    // floating above the talude. Wait until the bed has stamped.
    if (!GAME.isGroundReadyForPlacement(state)) return;
    // The prop GLBs carry KTX2 textures — wire the transcoder from the live
    // renderer before the first load or every one of them fails to parse.
    GAME.ensureKTX2LoaderReady(state);
    dressed = true;
    // The <Road flatten> carve registers its own `kind:'road'` ground brush
    // during setup. `avoid-road` reads `halfWidth` (bed + berm); trees plant
    // on the talude. `carveHalfWidth` anchors their Y to the analytic shelf.
    void dressTrack(
      scene as THREE.Object3D,
      spline,
      GAME.RaceTrackComponent.shoulder[trackEid] || 3,
      state
    ).catch((err) => {
      // `dressed` is already latched — without this catch a mid-walk throw
      // rejects unhandled and half the circuit stays bare with no retry.
      console.error('[track-props] dressing failed mid-circuit:', err);
    });
  },
};

async function dressTrack(
  scene: THREE.Object3D,
  spline: GAME.TrackSpline,
  shoulder: number,
  state: GAME.State
): Promise<void> {
  // Props are entities in the engine's instancing pool now, so this function
  // adds almost nothing to the scene graph itself — only the pole batch.
  // Light poles: alternate sides, every POLE_SPACING metres, all four parts
  // batched. Real PointLight entities only go up where they are actually seen
  // at night-ish exposure — the city and the stadium hairpin. A lamp entity
  // per pole around 5 km of circuit is 160 lights for a renderer that keeps
  // the 12 nearest.
  const poleCount = Math.ceil(spline.length / POLE_SPACING) + 2;
  const poles = createPoleBatch(poleCount);
  scene.add(poles.group);
  let poleIndex = 0;
  for (let s = 40; s < spline.length - 40; s += POLE_SPACING) {
    const frame = spline.sampleAt(s);
    const side = poleIndex % 2 === 0 ? -1 : 1;
    poleIndex++;
    const lateral = (frame.width * 0.5 + shoulder + 0.6) * side;
    const poleX = frame.x + frame.rx * lateral;
    const poleZ = frame.z + frame.rz * lateral;
    // The pole stands on the carved bed / terrain beside the barrier — the
    // spline height is the driving surface, which now sits above the ground
    // by the embankment, so ground the pole via the terrain sampler.
    const poleSurface = GAME.sampleTerrainSurface(state, poleX, poleZ, 0.75);
    const poleY = poleSurface?.worldY ?? frame.y;
    // Face the pole arm toward the track.
    const yaw = Math.atan2(frame.tx, frame.tz) + (side > 0 ? Math.PI : 0);
    poles.add(poleX, poleY, poleZ, yaw);

    const lit = frame.section === 'city' || frame.section === 'stadium';
    if (lit && poleIndex % POLE_LIGHT_EVERY === 0) {
      const eid = state.createEntity();
      state.addComponent(eid, GAME.PointLight);
      state.addComponent(eid, GAME.Transform);
      GAME.PointLight.color[eid] = LAMP_COLOR;
      GAME.PointLight.intensity[eid] = 22;
      GAME.PointLight.distance[eid] = 26;
      GAME.PointLight.decay[eid] = 2;
      const lampWorld = poles.lampAt(poleX, poleY, poleZ, yaw);
      GAME.Transform.posX[eid] = lampWorld.x;
      GAME.Transform.posY[eid] = lampWorld.y;
      GAME.Transform.posZ[eid] = lampWorld.z;
      GAME.Transform.dirty[eid] = 1;
    }
  }
  poles.finish();

  // City gate as start/finish gantry, sized to span the road (native arch is
  // 10 m wide with an ~8 m opening; the city straight is 22 m). Same width
  // trick as the old start banner: fit across `startFrame.width + 8`.
  const startFrame = spline.sampleAt(0);
  const banner = await measureProp(
    'infra',
    'city_gate_arch',
    startFrame.width + 8,
    'width',
    'across'
  );
  if (banner) {
    // The gantry's long side is on +X now, so aligning its local +Z with the
    // track tangent makes it span the road.
    placeProp(
      state,
      banner,
      startFrame.x,
      startFrame.y,
      startFrame.z,
      Math.atan2(startFrame.tx, startFrame.tz)
    );
  }

  // Everything else: walk the circuit and place per-section props on cadence.
  const cadence = new Map<string, number>();
  const step = 4;
  for (let s = 0; s < spline.length; s += step) {
    const frame = spline.sampleAt(s);
    const defs = SECTION_PROPS[frame.section] ?? DEFAULT_PROPS;
    const barrier = frame.width * 0.5 + shoulder;

    for (const def of defs) {
      const key = `${def.pack}/${def.file}:${def.offset}:${def.side}`;
      const acc = (cadence.get(key) ?? def.spacing) + step;
      if (acc < def.spacing) {
        cadence.set(key, acc);
        continue;
      }
      cadence.set(key, 0);

      const sides: number[] = def.side === 0 ? [-1, 1] : [def.side];
      // One measurement per GLB, cached; the placements below are just data.
      const fit = await measureProp(def.pack, def.file, def.height);
      if (!fit) continue;

      for (const side of sides) {
        const sizeScale = 1 + (def.sizeJitter ?? 0) * (Math.random() - 0.5) * 2;
        const lateral = (barrier + def.offset + Math.random() * 1.5) * side;
        const propX = frame.x + frame.rx * lateral;
        const propZ = frame.z + frame.rz * lateral;
        // Ground the prop on the terrain: the spline height is the driving
        // surface (embankment-suspended, and over the flyover it is 20 m of
        // air), so anything off the asphalt samples the bed instead of
        // inheriting the road height. `placeProp` re-samples; frame.y is only
        // the fallback when the heightfield is missing.
        placeProp(
          state,
          fit,
          propX,
          frame.y,
          propZ,
          Math.atan2(frame.tx, frame.tz) +
            (def.yawJitter ? (Math.random() - 0.5) * def.yawJitter : 0),
          sizeScale
        );

        if (def.solid) {
          GAME.addTrackObstacle(propX, propZ, def.solid, 0.5);
        }
      }
    }
  }
}

/** Reset the one-shot guard (used when the page hot-reloads the module). */
export function resetTrackDressing(): void {
  dressed = false;
  fits.clear();
  GAME.clearTrackObstacles();
}
