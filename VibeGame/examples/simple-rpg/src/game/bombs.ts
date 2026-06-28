// Bomb system. Tap [B]: drop a live bomb at the player's feet. Hold [B]: an
// aim arc appears (auto-targeting the nearest enemy); release to lob the bomb
// along that arc. On landing the fuse burns down (blinking telegraph) then it
// explodes — radial damage with falloff. The thrower (owner) is spared.
import * as THREE from 'three';
import {
  getScene,
  defineQuery,
  Health,
  Transform,
  damageHealth,
  isDead,
  spawnParticleBurst,
  spawnFloatingText,
  playSound,
} from 'vibegame';
import type { State } from 'vibegame';
import { heroStats } from './skills';

const FUSE_SECONDS = 1.5;
const BLAST_RADIUS = 6;
const BLAST_DAMAGE = 90; // at the centre; linear falloff to 30% at the edge
const ARC_SEGMENTS = 18;

interface Bomb {
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
  phase: 'flight' | 'fuse';
  fuse: number;
  // landing / explosion point
  x: number;
  y: number;
  z: number;
  // flight (thrown) state
  fx: number;
  fy: number;
  fz: number;
  apex: number;
  flightT: number;
  flightDur: number;
  owner: number;
}

const bombs: Bomb[] = [];
const healthQuery = defineQuery([Health, Transform]);

function arcHeight(dist: number): number {
  return Math.max(1.5, Math.min(6, dist * 0.28));
}

/** Parabola point from→to at u∈[0,1] with the given apex lift. */
function arcPoint(
  fx: number,
  fy: number,
  fz: number,
  tx: number,
  ty: number,
  tz: number,
  apex: number,
  u: number,
  out: THREE.Vector3
): THREE.Vector3 {
  const lift = apex * 4 * u * (1 - u);
  out.set(
    fx + (tx - fx) * u,
    fy + (ty - fy) * u + lift + 0.3,
    fz + (tz - fz) * u
  );
  return out;
}

function makeBombMesh(
  state: State,
  x: number,
  y: number,
  z: number
): {
  mesh: THREE.Mesh;
  mat: THREE.MeshStandardMaterial;
} | null {
  const scene = getScene(state);
  if (!scene) return null;
  const mat = new THREE.MeshStandardMaterial({
    color: 0x111111,
    emissive: 0xff3300,
    emissiveIntensity: 0.2,
    roughness: 0.5,
  });
  const mesh = new THREE.Mesh(new THREE.SphereGeometry(0.28, 16, 12), mat);
  mesh.position.set(x, y + 0.3, z);
  scene.add(mesh);
  return { mesh, mat };
}

/** Drop a live bomb at the player's feet (tap). */
export function spawnBomb(
  state: State,
  x: number,
  y: number,
  z: number,
  owner: number
): void {
  const m = makeBombMesh(state, x, y, z);
  if (!m) return;
  bombs.push({
    mesh: m.mesh,
    mat: m.mat,
    phase: 'fuse',
    fuse: FUSE_SECONDS,
    x,
    y,
    z,
    fx: x,
    fy: y,
    fz: z,
    apex: 0,
    flightT: 0,
    flightDur: 0,
    owner,
  });
  playSound('bomb-drop');
}

/** Lob a bomb along an arc from→to, then it lands and fuses (hold + release). */
export function throwBomb(
  state: State,
  fx: number,
  fy: number,
  fz: number,
  tx: number,
  ty: number,
  tz: number,
  owner: number
): void {
  const m = makeBombMesh(state, fx, fy, fz);
  if (!m) return;
  const dist = Math.hypot(tx - fx, tz - fz);
  bombs.push({
    mesh: m.mesh,
    mat: m.mat,
    phase: 'flight',
    fuse: FUSE_SECONDS,
    x: tx,
    y: ty,
    z: tz,
    fx,
    fy,
    fz,
    apex: arcHeight(dist),
    flightT: 0,
    flightDur: Math.max(0.4, Math.min(0.9, dist * 0.06)),
    owner,
  });
  playSound('swing');
}

const _p = new THREE.Vector3();

export function updateBombs(state: State, dt: number): void {
  for (let i = bombs.length - 1; i >= 0; i--) {
    const b = bombs[i];
    if (b.phase === 'flight') {
      b.flightT += dt;
      const u = Math.min(1, b.flightT / b.flightDur);
      arcPoint(b.fx, b.fy, b.fz, b.x, b.y, b.z, b.apex, u, _p);
      b.mesh.position.copy(_p);
      b.mesh.rotation.x += dt * 12;
      if (u >= 1) {
        b.phase = 'fuse';
        b.mesh.position.set(b.x, b.y + 0.3, b.z);
      }
      continue;
    }
    b.fuse -= dt;
    const rate = b.fuse < 0.4 ? 28 : b.fuse < 0.9 ? 14 : 7;
    b.mat.emissiveIntensity = 0.2 + (Math.sin(b.fuse * rate) > 0 ? 0.9 : 0.0);
    b.mesh.scale.setScalar(1 + (1 - b.fuse / FUSE_SECONDS) * 0.25);
    if (b.fuse <= 0) {
      explode(state, b);
      b.mesh.removeFromParent();
      b.mesh.geometry.dispose();
      b.mat.dispose();
      bombs.splice(i, 1);
    }
  }
}

function explode(state: State, b: Bomb): void {
  spawnParticleBurst(state, {
    x: b.x,
    y: b.y + 0.5,
    z: b.z,
    preset: 'explosion',
    count: 40,
    duration: 1.0,
  });
  playSound('mine-break');
  spawnFloatingText(state, '💥', {
    x: b.x,
    y: b.y + 1.4,
    z: b.z,
    color: '#ff8a2a',
    size: 0.8,
    duration: 0.8,
  });
  const merchantEid = state.getEntityByName('merchant');
  const baseDamage = BLAST_DAMAGE + heroStats.attackBonus;
  const r2 = BLAST_RADIUS * BLAST_RADIUS;
  for (const e of healthQuery(state.world)) {
    if (e === b.owner || e === merchantEid || isDead(e)) continue;
    const dx = Transform.posX[e] - b.x;
    const dz = Transform.posZ[e] - b.z;
    const d2 = dx * dx + dz * dz;
    if (d2 > r2) continue;
    const falloff = Math.max(0.3, 1 - Math.sqrt(d2) / BLAST_RADIUS);
    damageHealth(e, baseDamage * falloff);
  }
}

/** Nearest living enemy to `hero` within `maxRange` (XZ). 0 if none. */
export function nearestEnemy(
  state: State,
  hero: number,
  maxRange: number
): number {
  const hx = Transform.posX[hero];
  const hz = Transform.posZ[hero];
  const merchantEid = state.getEntityByName('merchant');
  let best = 0;
  let bestD2 = maxRange * maxRange;
  for (const e of healthQuery(state.world)) {
    if (e === hero || e === merchantEid || isDead(e)) continue;
    const dx = Transform.posX[e] - hx;
    const dz = Transform.posZ[e] - hz;
    const d2 = dx * dx + dz * dz;
    if (d2 < bestD2) {
      bestD2 = d2;
      best = e;
    }
  }
  return best;
}

// ── Aim arc preview ───────────────────────────────────────────────────────
let arcLine: THREE.Line | null = null;
let arcMarker: THREE.Mesh | null = null;

export function updateThrowArc(
  state: State,
  fx: number,
  fy: number,
  fz: number,
  tx: number,
  ty: number,
  tz: number
): void {
  const scene = getScene(state);
  if (!scene) return;
  if (!arcLine) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array((ARC_SEGMENTS + 1) * 3), 3)
    );
    arcLine = new THREE.Line(
      geo,
      new THREE.LineBasicMaterial({
        color: 0xffae3a,
        transparent: true,
        opacity: 0.9,
      })
    );
    arcLine.renderOrder = 997;
    scene.add(arcLine);
    arcMarker = new THREE.Mesh(
      new THREE.RingGeometry(0.5, 0.75, 20),
      new THREE.MeshBasicMaterial({
        color: 0xff5a3a,
        transparent: true,
        opacity: 0.85,
        side: THREE.DoubleSide,
      })
    );
    arcMarker.rotation.x = -Math.PI / 2;
    scene.add(arcMarker);
  }
  const apex = arcHeight(Math.hypot(tx - fx, tz - fz));
  const pos = arcLine.geometry.getAttribute(
    'position'
  ) as THREE.BufferAttribute;
  for (let i = 0; i <= ARC_SEGMENTS; i++) {
    arcPoint(fx, fy, fz, tx, ty, tz, apex, i / ARC_SEGMENTS, _p);
    pos.setXYZ(i, _p.x, _p.y, _p.z);
  }
  pos.needsUpdate = true;
  arcLine.visible = true;
  if (arcMarker) {
    arcMarker.position.set(tx, ty + 0.08, tz);
    arcMarker.visible = true;
  }
}

export function hideThrowArc(): void {
  if (arcLine) arcLine.visible = false;
  if (arcMarker) arcMarker.visible = false;
}

/** HMR/teardown cleanup. */
export function clearBombs(): void {
  for (const b of bombs) {
    b.mesh.removeFromParent();
    b.mesh.geometry.dispose();
    b.mat.dispose();
  }
  bombs.length = 0;
  if (arcLine) {
    arcLine.removeFromParent();
    arcLine = null;
  }
  if (arcMarker) {
    arcMarker.removeFromParent();
    arcMarker = null;
  }
}
