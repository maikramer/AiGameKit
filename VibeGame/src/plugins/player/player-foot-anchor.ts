import {
  Box3,
  Matrix4,
  type Bone,
  type Mesh,
  type Object3D,
  type SkinnedMesh,
  Vector3,
} from 'three';

export interface PlayerFootAnchor {
  /** Lift so soles sit at entity/root Y=0 (root-local). */
  yOffset: number;
  /** Root-local AABB of the posed mesh (before yOffset). */
  box: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
  };
  /** Lowest sole Y in root-local space. */
  soleY: number;
}

const _solePos = new Vector3();
const _box = new Box3();
const _localBox = new Box3();
const _invRoot = /*@__PURE__*/ new Matrix4();
const _corner = /*@__PURE__*/ new Vector3();
const _rootWorld = /*@__PURE__*/ new Vector3();

function isSoleBone(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n.includes('ball_') ||
    n.includes('ball.') ||
    n.includes('toe') ||
    n.endsWith('_ball') ||
    n === 'ball_l' ||
    n === 'ball_r'
  );
}

function isFootBone(name: string): boolean {
  const n = name.toLowerCase();
  return (
    n === 'foot_l' ||
    n === 'foot_r' ||
    n.endsWith('foot_l') ||
    n.endsWith('foot_r') ||
    n.includes('foot_') ||
    (n.includes('foot') && !n.includes('toe'))
  );
}

function worldBoxToRootLocal(world: Box3, invRoot: Matrix4, out: Box3): void {
  out.makeEmpty();
  const { min, max } = world;
  for (let ix = 0; ix < 2; ix++) {
    for (let iy = 0; iy < 2; iy++) {
      for (let iz = 0; iz < 2; iz++) {
        _corner.set(
          ix === 0 ? min.x : max.x,
          iy === 0 ? min.y : max.y,
          iz === 0 ? min.z : max.z
        );
        _corner.applyMatrix4(invRoot);
        out.expandByPoint(_corner);
      }
    }
  }
}

/**
 * Anchor so **soles** sit on the entity/root origin (Y=0), not the pelvis.
 * Results are always in **root-local** space — safe whether `root` is still at
 * the origin (PlayerGLTF load) or already planted in the world (Creature XML).
 */
export function computePlayerFootAnchor(root: Object3D): PlayerFootAnchor {
  root.updateMatrixWorld(true);
  _invRoot.copy(root.matrixWorld).invert();
  root.getWorldPosition(_rootWorld);

  root.traverse((obj) => {
    const skinned = obj as SkinnedMesh;
    if (skinned.isSkinnedMesh) {
      skinned.skeleton?.update();
      skinned.computeBoundingBox();
    }
  });

  _box.makeEmpty();
  let hasMesh = false;
  root.traverse((obj) => {
    const skinned = obj as SkinnedMesh;
    if (skinned.isSkinnedMesh && skinned.boundingBox) {
      const b = skinned.boundingBox.clone().applyMatrix4(skinned.matrixWorld);
      _box.union(b);
      hasMesh = true;
      return;
    }
    const mesh = obj as Mesh;
    if (mesh.isMesh && !skinned.isSkinnedMesh) {
      _box.expandByObject(mesh);
      hasMesh = true;
    }
  });
  if (!hasMesh || _box.isEmpty()) {
    _box.setFromObject(root);
  }

  let soleYWorld = Infinity;
  let ankleYWorld = Infinity;
  root.traverse((obj) => {
    const bone = obj as Bone;
    if (!bone.isBone) return;
    bone.getWorldPosition(_solePos);
    if (isSoleBone(bone.name)) {
      soleYWorld = Math.min(soleYWorld, _solePos.y);
    } else if (isFootBone(bone.name)) {
      ankleYWorld = Math.min(ankleYWorld, _solePos.y);
    }
  });

  const meshMinYWorld = Number.isFinite(_box.min.y) ? _box.min.y : _rootWorld.y;
  const boneSoleWorld = Number.isFinite(soleYWorld)
    ? soleYWorld
    : Number.isFinite(ankleYWorld)
      ? ankleYWorld
      : meshMinYWorld;
  const groundYWorld = Math.min(boneSoleWorld, meshMinYWorld);

  worldBoxToRootLocal(_box, _invRoot, _localBox);
  // Root-local sole: world sole → subtract root world Y under upright plant
  // (full inverse on a point if the root has pitch/roll).
  _solePos.set(_rootWorld.x, groundYWorld, _rootWorld.z);
  _solePos.applyMatrix4(_invRoot);
  const soleY = Number.isFinite(_solePos.y) ? _solePos.y : _localBox.min.y;
  const yOffset = Number.isFinite(soleY) ? -soleY : 0;

  return {
    yOffset,
    soleY,
    box: {
      min: { x: _localBox.min.x, y: _localBox.min.y, z: _localBox.min.z },
      max: { x: _localBox.max.x, y: _localBox.max.y, z: _localBox.max.z },
    },
  };
}
