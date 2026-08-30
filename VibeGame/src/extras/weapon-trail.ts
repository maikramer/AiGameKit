import * as THREE from 'three';

/**
 * Ribbon that follows a swinging blade and fades behind it.
 *
 * A melee swing is over in a fifth of a second, which is not long enough for
 * the eye to read the arc from the skinned mesh alone — impact particles fire
 * *after* contact and say nothing about where the weapon travelled. The trail
 * is the part that sells the swing: it draws the path the blade already took,
 * so the motion stays legible even at 30fps and even when the hit lands off
 * screen edge.
 *
 * Each frame the caller pushes the blade's two world-space endpoints (hilt and
 * tip). Samples are kept in a ring buffer and rebuilt into one triangle strip
 * whose per-vertex alpha decays with age, so the ribbon dissolves from the
 * tail. Nothing here knows about the player, weapons, or the ECS: hand it two
 * points a frame and add {@link WeaponTrail.object3D} to a scene.
 */
export interface WeaponTrailOptions {
  /** Ring-buffer length; the trail spans at most this many samples. */
  segments?: number;
  /** Seconds a sample stays visible before it has fully faded. */
  lifetime?: number;
  /** Ribbon tint. */
  color?: number;
  /** Alpha at the head of the ribbon (the newest sample). */
  opacity?: number;
  /**
   * Minimum tip travel between samples, in metres. A weapon that is barely
   * moving would otherwise stack samples on one spot and read as a blob.
   */
  minDistance?: number;
  /** Additive blending (bright, energy-blade look) vs normal alpha. */
  additive?: boolean;
  /** Render order for the ribbon mesh (default 10, over most opaques). */
  renderOrder?: number;
}

interface TrailSample {
  bx: number;
  by: number;
  bz: number;
  tx: number;
  ty: number;
  tz: number;
  time: number;
}

const DEFAULTS = {
  segments: 14,
  lifetime: 0.16,
  color: 0xffffff,
  opacity: 0.55,
  minDistance: 0.02,
  additive: true,
  renderOrder: 10,
};

const VERTEX_SHADER = `
attribute float aAlpha;
varying float vAlpha;
void main() {
  vAlpha = aAlpha;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const FRAGMENT_SHADER = `
uniform vec3 uColor;
varying float vAlpha;
void main() {
  if (vAlpha <= 0.001) discard;
  gl_FragColor = vec4(uColor, vAlpha);
}
`;

export class WeaponTrail {
  readonly object3D: THREE.Mesh;

  private readonly geometry: THREE.BufferGeometry;
  private readonly material: THREE.ShaderMaterial;
  private readonly positions: Float32Array;
  private readonly alphas: Float32Array;
  private readonly samples: TrailSample[] = [];
  private readonly segments: number;
  private readonly lifetime: number;
  private readonly opacity: number;
  private readonly minDistanceSq: number;

  constructor(options: WeaponTrailOptions = {}) {
    this.segments = Math.max(
      2,
      Math.floor(options.segments ?? DEFAULTS.segments)
    );
    this.lifetime = Math.max(0.01, options.lifetime ?? DEFAULTS.lifetime);
    this.opacity = Math.max(0, options.opacity ?? DEFAULTS.opacity);
    const minDistance = Math.max(
      0,
      options.minDistance ?? DEFAULTS.minDistance
    );
    this.minDistanceSq = minDistance * minDistance;

    const vertexCount = this.segments * 2;
    this.positions = new Float32Array(vertexCount * 3);
    this.alphas = new Float32Array(vertexCount);

    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute(
      'position',
      new THREE.BufferAttribute(this.positions, 3)
    );
    this.geometry.setAttribute(
      'aAlpha',
      new THREE.BufferAttribute(this.alphas, 1)
    );

    // Static index buffer for the whole ribbon; `drawRange` clips it to the
    // samples that are actually live, so a partially filled trail costs only
    // the triangles it uses and no buffer is ever re-uploaded.
    const indices: number[] = [];
    for (let i = 0; i < this.segments - 1; i++) {
      const a = i * 2;
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    this.geometry.setIndex(indices);
    this.geometry.setDrawRange(0, 0);

    this.material = new THREE.ShaderMaterial({
      uniforms: {
        uColor: { value: new THREE.Color(options.color ?? DEFAULTS.color) },
      },
      vertexShader: VERTEX_SHADER,
      fragmentShader: FRAGMENT_SHADER,
      transparent: true,
      depthWrite: false,
      side: THREE.DoubleSide,
      blending:
        (options.additive ?? DEFAULTS.additive)
          ? THREE.AdditiveBlending
          : THREE.NormalBlending,
    });

    this.object3D = new THREE.Mesh(this.geometry, this.material);
    // Samples are world-space, so the mesh must never inherit a transform, and
    // its bounds change every frame — culling it by a stale sphere would blink
    // the ribbon out mid-swing.
    this.object3D.frustumCulled = false;
    this.object3D.matrixAutoUpdate = false;
    this.object3D.renderOrder = options.renderOrder ?? DEFAULTS.renderOrder;
    this.object3D.name = 'weapon-trail';
  }

  /** Live sample count (0 when the ribbon has fully faded). */
  get sampleCount(): number {
    return this.samples.length;
  }

  /** Colour of the ribbon (accepts anything `THREE.Color.set` takes). */
  setColor(color: THREE.ColorRepresentation): void {
    (this.material.uniforms.uColor!.value as THREE.Color).set(color);
  }

  /**
   * Record where the blade is this frame. `time` is a monotonic clock in
   * seconds (the ECS `state.time.elapsed` works). Samples closer than
   * `minDistance` to the previous one are dropped so a resting weapon does not
   * pile up geometry.
   */
  push(base: THREE.Vector3, tip: THREE.Vector3, time: number): void {
    const last = this.samples[this.samples.length - 1];
    if (last) {
      const dx = tip.x - last.tx;
      const dy = tip.y - last.ty;
      const dz = tip.z - last.tz;
      if (dx * dx + dy * dy + dz * dz < this.minDistanceSq) return;
    }
    this.samples.push({
      bx: base.x,
      by: base.y,
      bz: base.z,
      tx: tip.x,
      ty: tip.y,
      tz: tip.z,
      time,
    });
    if (this.samples.length > this.segments) this.samples.shift();
    this.rebuild(time);
  }

  /**
   * Age the ribbon without adding a sample — call it on the frames after the
   * swing ends so the tail fades out instead of hanging in the air.
   */
  update(time: number): void {
    this.rebuild(time);
  }

  /** Drop every sample immediately (weapon stowed, teleport, death). */
  clear(): void {
    this.samples.length = 0;
    this.geometry.setDrawRange(0, 0);
  }

  dispose(): void {
    this.object3D.removeFromParent();
    this.geometry.dispose();
    this.material.dispose();
  }

  private rebuild(now: number): void {
    // Expire from the front: samples are chronological, so the first survivor
    // ends the sweep.
    while (
      this.samples.length > 0 &&
      now - this.samples[0]!.time >= this.lifetime
    ) {
      this.samples.shift();
    }
    const n = this.samples.length;
    if (n < 2) {
      this.geometry.setDrawRange(0, 0);
      return;
    }

    for (let i = 0; i < n; i++) {
      const s = this.samples[i]!;
      const p = i * 6;
      this.positions[p] = s.bx;
      this.positions[p + 1] = s.by;
      this.positions[p + 2] = s.bz;
      this.positions[p + 3] = s.tx;
      this.positions[p + 4] = s.ty;
      this.positions[p + 5] = s.tz;

      const age = (now - s.time) / this.lifetime;
      const alpha = Math.max(0, 1 - age) * this.opacity;
      // The blade edge stays brighter than the hilt side: the ribbon reads as
      // a swept edge rather than a flat sheet.
      this.alphas[i * 2] = alpha * 0.35;
      this.alphas[i * 2 + 1] = alpha;
    }

    (this.geometry.attributes.position as THREE.BufferAttribute).needsUpdate =
      true;
    (this.geometry.attributes.aAlpha as THREE.BufferAttribute).needsUpdate =
      true;
    this.geometry.setDrawRange(0, (n - 1) * 6);
  }
}

const _blade = new THREE.Vector3();
const _base = new THREE.Vector3();
const _tip = new THREE.Vector3();
const _boxSize = new THREE.Vector3();
const _box = new THREE.Box3();

/**
 * World-space hilt/tip of a held weapon object, from its own geometry.
 *
 * Weapon GLBs are modelled along their longest local axis; measuring the
 * object's local bounding box each time it changes means one grip tweak or one
 * swapped weapon does not need a hand-maintained length constant. The returned
 * vectors are shared scratch — copy them if you keep them.
 */
export function bladeEndpoints(
  object: THREE.Object3D,
  options?: {
    /**
     * Push the tip out (and the hilt end in) by this fraction of the blade's
     * length. A ribbon that stops exactly at the modelled tip reads as a
     * smear stuck to the mesh; a little overshoot reads as a swept edge.
     */
    extend?: number;
    /**
     * Pull the hilt end up toward the tip by this fraction of the blade's
     * length. A ribbon anchored at the grip fans into a wide sheet that hides
     * the character; anchoring partway up the blade keeps it an edge trail.
     */
    inset?: number;
  }
): {
  base: THREE.Vector3;
  tip: THREE.Vector3;
} | null {
  _box.setFromObject(object, true);
  if (_box.isEmpty()) return null;
  object.updateWorldMatrix(true, false);

  // Longest local axis = the blade. Measure in local space so the rig's pose
  // does not rotate the choice frame to frame.
  _box.getSize(_boxSize);
  const axis =
    _boxSize.x > _boxSize.y
      ? _boxSize.x > _boxSize.z
        ? 'x'
        : 'z'
      : _boxSize.y > _boxSize.z
        ? 'y'
        : 'z';
  const half = _boxSize[axis] * 0.5;
  if (half <= 0) return null;

  const extend = options?.extend ?? 0;
  const inset = Math.max(0, Math.min(0.95, options?.inset ?? 0));
  _blade.set(axis === 'x' ? 1 : 0, axis === 'y' ? 1 : 0, axis === 'z' ? 1 : 0);
  const baseEnd = -half * (1 - extend);
  const tipEnd = half * (1 + extend);
  _base
    .copy(_blade)
    .multiplyScalar(baseEnd + (tipEnd - baseEnd) * inset)
    .applyMatrix4(object.matrixWorld);
  _tip.copy(_blade).multiplyScalar(tipEnd).applyMatrix4(object.matrixWorld);
  return { base: _base, tip: _tip };
}
