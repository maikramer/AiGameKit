import { describe, expect, it } from 'bun:test';
import {
  AnimationClip,
  BooleanKeyframeTrack,
  Scene,
  NumberKeyframeTrack,
  QuaternionKeyframeTrack,
  VectorKeyframeTrack,
} from 'three';
import { GltfAnimator, mirrorAnimationClip, mirrorBoneName } from 'vibegame';

/** Skeletal clip with one lateral bone pair + trunk, quat+pos+scale tracks. */
function makeSlashClip(name: string): AnimationClip {
  const times = [0, 0.5, 1];
  const tracks = [
    new QuaternionKeyframeTrack(
      'upperarm_l.quaternion',
      times,
      [0, 0, 0, 1, 0.1, 0.2, 0.3, 0.9, 0, 0, 0, 1].map((v) => v)
    ),
    new QuaternionKeyframeTrack(
      'upperarm_r.quaternion',
      times,
      [1, 0, 0, 0, 0.5, 0.5, 0.5, 0.5, 1, 0, 0, 0]
    ),
    new QuaternionKeyframeTrack(
      'spine_01.quaternion',
      times,
      [0, 0, 0, 1, 0, 0.1, 0, 1, 0, 0, 0, 1]
    ),
    new VectorKeyframeTrack(
      'pelvis.position',
      times,
      [0.1, 0.9, 0, -0.1, 0.8, 0.05, 0, 0.85, 0]
    ),
    new VectorKeyframeTrack(
      'upperarm_l.scale',
      times,
      [1, 1, 1, 1, 1, 1, 1, 1, 1]
    ),
  ];
  return new AnimationClip(name, 1, tracks);
}

describe('mirrorBoneName', () => {
  it('swaps UE5 lateral suffixes', () => {
    expect(mirrorBoneName('upperarm_l')).toBe('upperarm_r');
    expect(mirrorBoneName('upperarm_r')).toBe('upperarm_l');
    expect(mirrorBoneName('calf_l')).toBe('calf_r');
  });

  it('swaps Mixamo Left/Right prefixes', () => {
    expect(mirrorBoneName('LeftArm')).toBe('RightArm');
    expect(mirrorBoneName('RightForeArm')).toBe('LeftForeArm');
  });

  it('swaps Blender .L/.R suffixes', () => {
    expect(mirrorBoneName('hand.L')).toBe('hand.R');
  });

  it('keeps non-lateral bones unchanged', () => {
    expect(mirrorBoneName('spine_01')).toBe('spine_01');
    expect(mirrorBoneName('Head')).toBe('Head');
    expect(mirrorBoneName('pelvis')).toBe('pelvis');
  });
});

describe('mirrorAnimationClip', () => {
  const clip = makeSlashClip('sworda');
  const mirrored = mirrorAnimationClip(clip, 'sworda_m');

  it('derives the mirrored name from the source when omitted', () => {
    expect(mirrorAnimationClip(clip).name).toBe('sworda_m');
  });

  it('swaps lateral bone track names and keeps trunk names', () => {
    const names = mirrored.tracks.map((t) => t.name);
    expect(names).toContain('upperarm_r.quaternion');
    expect(names).toContain('upperarm_l.quaternion');
    expect(names).toContain('spine_01.quaternion');
  });

  it('reflects quaternions as (w,x,-y,-z)', () => {
    const track = mirrored.tracks.find(
      (t) => t.name === 'upperarm_r.quaternion'
    );
    const v = track!.values as Float32Array;
    // source upperarm_l key 1 was (0.1, 0.2, 0.3, 0.9) → (0.1, 0.2, -0.3, -0.9)
    expect(v[4]).toBeCloseTo(0.1);
    expect(v[5]).toBeCloseTo(0.2);
    expect(v[6]).toBeCloseTo(-0.3);
    expect(v[7]).toBeCloseTo(-0.9);
  });

  it('negates the lateral position component only', () => {
    const track = mirrored.tracks.find((t) => t.name === 'pelvis.position');
    const v = track!.values as Float32Array;
    expect(v[0]).toBeCloseTo(-0.1);
    expect(v[1]).toBeCloseTo(0.9);
    expect(v[2]).toBeCloseTo(0);
  });

  it('leaves scale tracks untouched', () => {
    const track = mirrored.tracks.find((t) => t.name === 'upperarm_r.scale');
    const v = track!.values as Float32Array;
    expect(v[0]).toBeCloseTo(1);
    expect(v[1]).toBeCloseTo(1);
    expect(v[2]).toBeCloseTo(1);
  });

  it('keeps duration and does not mutate the source clip', () => {
    expect(mirrored.duration).toBe(clip.duration);
    const srcTrack = clip.tracks.find(
      (t) => t.name === 'upperarm_l.quaternion'
    );
    const v = srcTrack!.values as Float32Array;
    expect(v[6]).toBeCloseTo(0.3); // sinal original intacto
  });

  it('passes through non-skeletal tracks untouched', () => {
    const withMisc = new AnimationClip('misc', 1, [
      new NumberKeyframeTrack('material.opacity', [0, 1], [0, 1]),
      new BooleanKeyframeTrack('mesh.visible', [0], [true]),
    ]);
    const out = mirrorAnimationClip(withMisc);
    expect(out.tracks.map((t) => t.name)).toEqual([
      'material.opacity',
      'mesh.visible',
    ]);
  });
});

describe('GltfAnimator.ensureMirroredClip', () => {
  function makeAnimator() {
    const gltf = {
      scene: new Scene(),
      animations: [makeSlashClip('sworda')],
    };
    return new GltfAnimator(gltf as any);
  }

  it('registers the mirrored variant on demand', () => {
    const animator = makeAnimator();
    expect(animator.clipNames).not.toContain('sworda_m');
    expect(animator.ensureMirroredClip('sworda_m')).toBe(true);
    expect(animator.clipNames).toContain('sworda_m');
  });

  it('is idempotent and returns true when the clip exists', () => {
    const animator = makeAnimator();
    animator.ensureMirroredClip('sworda_m');
    expect(animator.ensureMirroredClip('sworda_m')).toBe(true);
  });

  it('returns false without a _m suffix or missing base clip', () => {
    const animator = makeAnimator();
    expect(animator.ensureMirroredClip('sworda')).toBe(true); // já existe
    expect(animator.ensureMirroredClip('swordb_m')).toBe(false);
  });

  it('resolveClipName auto-builds mirrored variants', () => {
    const animator = makeAnimator();
    expect(animator.resolveClipName('sworda_m')).toBe('sworda_m');
    expect(animator.clips.has('sworda_m')).toBe(true);
  });
});
