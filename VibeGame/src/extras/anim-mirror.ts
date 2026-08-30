// Left-right mirroring of skeletal animation clips (the "_m" convention).
//
// A mirrored clip is the same performance reflected across the sagittal plane:
// a right-handed sword slash becomes a left-handed one. This doubles melee
// variety for free (combos that alternate hands indefinitely) without any
// extra source clips.
//
// How it works (bone-local space, valid for symmetric rigs — Quaternius/UE5
// naming, Mixamo Left/Right, Blender .L/.R):
//   - every left bone track is re-bound to its right counterpart (and vice
//     versa): `upperarm_l` ↔ `upperarm_r`, `LeftArm` ↔ `RightArm`, …
//   - rotations are reflected through the mirror plane: quaternion
//     (w, x, y, z) → (w, x, −y, −z)  [conjugation by diag(−1,1,1)]
//   - positions negate the lateral axis component: (x, y, z) → (−x, y, z)
//   - non-lateral bones (spine, head, pelvis) keep their names and get
//     mirrored values, so the whole pose flips coherently.
import * as THREE from 'three';
import type { AnimationClip, KeyframeTrack } from 'three';

/** Left/right naming pairs whose tracks swap when mirroring. */
const LATERAL_PAIRS: ReadonlyArray<readonly [suffix: string, mirror: string]> =
  [
    ['_l', '_r'],
    ['_L', '_R'],
    ['.L', '.R'],
    ['_left', '_right'],
  ];

const LATERAL_PREFIXES: ReadonlyArray<
  readonly [prefix: string, mirror: string]
> = [
  ['Left', 'Right'],
  ['left', 'right'],
];

/** Mirror a bone name across the sagittal plane (identity if non-lateral). */
export function mirrorBoneName(name: string): string {
  for (const [l, r] of LATERAL_PAIRS) {
    if (name.endsWith(l)) return name.slice(0, -l.length) + r;
    if (name.endsWith(r)) return name.slice(0, -r.length) + l;
  }
  for (const [l, r] of LATERAL_PREFIXES) {
    if (name.startsWith(l)) return r + name.slice(l.length);
    if (name.startsWith(r)) return l + name.slice(r.length);
  }
  return name;
}

/** Reflect a packed quaternion track in place: (w,x,y,z) → (w,x,−y,−z). */
function mirrorQuaternionValues(values: Float32Array | number[]): void {
  for (let i = 0; i < values.length; i += 4) {
    values[i + 2] = -values[i + 2];
    values[i + 3] = -values[i + 3];
  }
}

/** Negate the lateral position component in place: (x,y,z) → (−x,y,z). */
function mirrorPositionValues(values: Float32Array | number[]): void {
  for (let i = 0; i < values.length; i += 3) {
    values[i] = -values[i];
  }
}

/**
 * Build a left-right mirrored copy of a skeletal AnimationClip.
 *
 * Mirroring is exact for symmetric rigs; asymmetric rest offsets (twisted
 * spines, props baked in one hand) come through as-is on the other side.
 */
export function mirrorAnimationClip(
  clip: AnimationClip,
  name?: string
): AnimationClip {
  const tracks: KeyframeTrack[] = [];
  for (const track of clip.tracks) {
    const dot = track.name.lastIndexOf('.');
    if (dot < 0) {
      tracks.push(track.clone());
      continue;
    }
    const bone = track.name.slice(0, dot);
    const prop = track.name.slice(dot + 1);
    const mirrored = track.clone();
    const values = Array.isArray(mirrored.values)
      ? mirrored.values.slice()
      : new Float32Array(mirrored.values);
    if (prop === 'quaternion') {
      mirrorQuaternionValues(values);
    } else if (prop === 'position') {
      mirrorPositionValues(values);
    } // scale tracks pass through unchanged
    mirrored.values = values as typeof mirrored.values;
    mirrored.name = `${mirrorBoneName(bone)}.${prop}`;
    tracks.push(mirrored);
  }
  const out = new THREE.AnimationClip(
    name ?? `${clip.name}_m`,
    clip.duration,
    tracks,
    clip.blendMode
  );
  return out;
}
