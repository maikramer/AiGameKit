import * as THREE from 'three';

/**
 * Fitting generated models into a scene that has a forward direction.
 *
 * Engine convention: **+Z is forward, +X is right, +Y is up.** Assets that come
 * out of a text-to-3D pipeline have no such convention — they arrive at an
 * arbitrary scale, sometimes Z-up, and pointing whichever way the generator
 * felt like. Bounding boxes are not enough to fix the heading: a kart measures
 * 2.38 m across and 2.69 m long, so "the longer side is the length" picks the
 * right answer by 13% — and picks the wrong one as soon as the model has a wide
 * rear axle or a spoiler.
 *
 * So the heading is measured from the geometry itself: the dominant horizontal
 * axis of the vertex cloud (a 2×2 PCA in the XZ plane), with the narrower end
 * taken as the front. That is stable for cars, signs, gantries and trees alike.
 */

/** Result of measuring a model's horizontal shape. */
export interface ModelAxis {
  /**
   * Yaw of the dominant horizontal axis, in radians, measured the same way the
   * engine measures headings: `atan2(x, z)`, so 0 means the axis already runs
   * along +Z.
   */
  angle: number;
  /** Length along the dominant axis (m, in the model's current scale). */
  length: number;
  /** Width across it (m). */
  width: number;
  /** How elongated the model is (length / width); ~1 means "no clear axis". */
  elongation: number;
}

/** Collect up to `budget` vertex positions in the object's local frame. */
function sampleLocalPoints(root: THREE.Object3D, budget = 3000): Float32Array {
  const meshes: THREE.Mesh[] = [];
  root.updateMatrixWorld(true);
  root.traverse((o) => {
    const mesh = o as THREE.Mesh;
    if (mesh.isMesh && mesh.geometry?.attributes?.position) meshes.push(mesh);
  });
  if (meshes.length === 0) return new Float32Array(0);

  let total = 0;
  for (const mesh of meshes) {
    total += (mesh.geometry.attributes.position as THREE.BufferAttribute).count;
  }
  const stride = Math.max(1, Math.floor(total / budget));

  const out: number[] = [];
  const inverse = new THREE.Matrix4().copy(root.matrixWorld).invert();
  const relative = new THREE.Matrix4();
  const v = new THREE.Vector3();
  for (const mesh of meshes) {
    const position = mesh.geometry.attributes.position as THREE.BufferAttribute;
    relative.multiplyMatrices(inverse, mesh.matrixWorld);
    for (let i = 0; i < position.count; i += stride) {
      v.fromBufferAttribute(position, i).applyMatrix4(relative);
      out.push(v.x, v.z);
    }
  }
  return Float32Array.from(out);
}

/**
 * Measure the model's dominant horizontal axis (2×2 PCA over the XZ plane).
 *
 * The returned `angle` also resolves the 180° ambiguity: the end whose cross
 * section is narrower is taken to be the front, which is true of a car's nose,
 * a sign's post and a tree's trunk alike.
 */
export function measureModelAxis(root: THREE.Object3D): ModelAxis {
  const points = sampleLocalPoints(root);
  const n = points.length / 2;
  if (n < 8) return { angle: 0, length: 0, width: 0, elongation: 1 };

  let meanX = 0;
  let meanZ = 0;
  for (let i = 0; i < n; i++) {
    meanX += points[i * 2]!;
    meanZ += points[i * 2 + 1]!;
  }
  meanX /= n;
  meanZ /= n;

  let cxx = 0;
  let czz = 0;
  let cxz = 0;
  for (let i = 0; i < n; i++) {
    const dx = points[i * 2]! - meanX;
    const dz = points[i * 2 + 1]! - meanZ;
    cxx += dx * dx;
    czz += dz * dz;
    cxz += dx * dz;
  }
  cxx /= n;
  czz /= n;
  cxz /= n;

  // Dominant eigenvector of [[cxx, cxz], [cxz, czz]].
  const theta = 0.5 * Math.atan2(2 * cxz, cxx - czz);
  // `theta` is measured from +X; convert to a direction vector, then to the
  // engine's atan2(x, z) heading convention.
  let axisX = Math.cos(theta);
  let axisZ = Math.sin(theta);

  // Extent along and across the axis.
  let minU = Infinity;
  let maxU = -Infinity;
  let maxV = 0;
  for (let i = 0; i < n; i++) {
    const dx = points[i * 2]! - meanX;
    const dz = points[i * 2 + 1]! - meanZ;
    const u = dx * axisX + dz * axisZ;
    const v = -dx * axisZ + dz * axisX;
    if (u < minU) minU = u;
    if (u > maxU) maxU = u;
    if (Math.abs(v) > maxV) maxV = Math.abs(v);
  }
  const length = maxU - minU;
  const width = maxV * 2;

  // Which end is the front? Compare the cross-section of the outer quarter at
  // each end; the narrower one is the nose.
  let frontSpread = 0;
  let frontCount = 0;
  let backSpread = 0;
  let backCount = 0;
  const frontCut = maxU - length * 0.25;
  const backCut = minU + length * 0.25;
  for (let i = 0; i < n; i++) {
    const dx = points[i * 2]! - meanX;
    const dz = points[i * 2 + 1]! - meanZ;
    const u = dx * axisX + dz * axisZ;
    const v = Math.abs(-dx * axisZ + dz * axisX);
    if (u >= frontCut) {
      frontSpread += v;
      frontCount++;
    } else if (u <= backCut) {
      backSpread += v;
      backCount++;
    }
  }
  const frontMean = frontCount > 0 ? frontSpread / frontCount : 0;
  const backMean = backCount > 0 ? backSpread / backCount : 0;
  if (backMean < frontMean) {
    axisX = -axisX;
    axisZ = -axisZ;
  }

  return {
    angle: Math.atan2(axisX, axisZ),
    length,
    width,
    elongation: width > 1e-4 ? length / width : 1,
  };
}

export interface FitModelOptions {
  /** Which engine axis the model's long side should end up on. */
  align?: 'forward' | 'across';
  /** Scale so this dimension matches: the model's height, length or width. */
  fit?: 'height' | 'length' | 'width';
  /** Target size in metres for whichever dimension `fit` names. */
  size?: number;
  /** Extra yaw in degrees applied after alignment (nose flips, fine tuning). */
  yawDegrees?: number;
  /** Seat the model's base on y = 0 and centre it on X/Z. */
  ground?: boolean;
  /**
   * Skip the PCA alignment when the model is this round in plan view. A boulder
   * has no meaningful heading and rotating it is just noise.
   */
  minElongation?: number;
  /**
   * Whether a model whose Z span dwarfs the others is treated as exported Z-up
   * and stood upright. `'never'` for assets you know arrive the right way up.
   */
  standUp?: 'auto' | 'never';
}

/**
 * Bring a generated model into the engine's conventions: standing upright,
 * pointing the right way, scaled to a real-world size, and seated on the
 * ground at its own origin.
 *
 * Returns the measured axis so callers can log or assert on it.
 */
export function fitModel(
  root: THREE.Object3D,
  options: FitModelOptions = {}
): ModelAxis {
  const {
    align = 'forward',
    fit = 'height',
    size,
    yawDegrees = 0,
    ground = true,
    minElongation = 1.08,
    standUp = 'auto',
  } = options;

  root.updateMatrixWorld(true);
  let box = new THREE.Box3().setFromObject(root);
  let extent = box.getSize(new THREE.Vector3());

  // 1. Stand it up if it was exported Z-up (a tree lying on its side).
  //
  // The test is deliberately strict. A lying tree is dramatic — 9 m on Z
  // against 2 m on X and Y — whereas a car is 2.7 long, 2.4 wide and 1.5 tall,
  // and a loose threshold stands it on its nose. Only a model whose Z span
  // dwarfs *both* other axes is treated as fallen over.
  if (
    standUp !== 'never' &&
    extent.z > extent.y * 2 &&
    extent.z > extent.x * 1.5
  ) {
    root.rotation.x -= Math.PI / 2;
  } else if (
    standUp !== 'never' &&
    extent.y > extent.z * 1.2 &&
    extent.y > extent.x * 1.3 &&
    // A tree/barrel already standing on +Y is also "taller than wide".
    // That is a column, not a bpy Y↔Z vehicle. Laying it down made
    // `measureProp` copy a ~4 m `groundOffset` onto an upright instanced
    // GLB whose feet were already at the origin — trunks in the air.
    !(extent.y >= extent.x * 1.5 && extent.y >= extent.z * 1.5)
  ) {
    // Length baked into Y (typical bpy glTF Y↔Z swap on a long vehicle).
    // A correct wagon is longer than it is tall, so this does not fire on a
    // mesh that already sits on its wheels.
    root.rotation.x -= Math.PI / 2;
  }

  // 2. Point it the right way, measured from the geometry rather than the box.
  const axis = measureModelAxis(root);
  if (axis.elongation >= minElongation) {
    // `axis.angle` is where the long side currently points; rotating by its
    // negative brings it onto +Z, and a further quarter turn puts it on +X.
    const target = align === 'forward' ? 0 : Math.PI / 2;
    root.rotation.y += target - axis.angle;
  }
  if (yawDegrees) root.rotation.y += (yawDegrees * Math.PI) / 180;
  root.updateMatrixWorld(true);
  box = new THREE.Box3().setFromObject(root);
  extent = box.getSize(new THREE.Vector3());

  // 3. Scale to the requested real-world size.
  if (size && size > 0) {
    const current =
      fit === 'height'
        ? extent.y
        : fit === 'length'
          ? Math.max(extent.z, 1e-4)
          : Math.max(extent.x, 1e-4);
    const scale = size / Math.max(current, 1e-4);
    if (Number.isFinite(scale) && scale > 0) {
      root.scale.multiplyScalar(scale);
      root.updateMatrixWorld(true);
      box = new THREE.Box3().setFromObject(root);
    }
  }

  // 4. Sit it on the ground, centred on its own footprint.
  if (ground) {
    const centre = box.getCenter(new THREE.Vector3());
    root.position.x -= centre.x;
    root.position.z -= centre.z;
    root.position.y -= box.min.y;
  }

  return axis;
}
