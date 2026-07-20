import {
  Box3,
  type Bone,
  type Mesh,
  type Object3D,
  type SkinnedMesh,
  Vector3,
} from 'three';

export interface PlayerFootAnchor {
  /** Lift applied to the visual root so soles sit at entity Y=0. */
  yOffset: number;
  /** World-space AABB of the posed mesh (before yOffset). */
  box: {
    min: { x: number; y: number; z: number };
    max: { x: number; y: number; z: number };
  };
  /** Lowest sole Y in root space (bone ball/toe preferred, else mesh min). */
  soleY: number;
}

const _solePos = new Vector3();
const _box = new Box3();

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

/**
 * Anchor the player so the **soles** sit on the entity origin (Y=0), not the
 * pelvis/waist. Skinned avatars often have the armature root near the hips;
 * mesh AABB alone can miss that — prefer ball/toe bone world Y when present.
 */
export function computePlayerFootAnchor(root: Object3D): PlayerFootAnchor {
  root.updateMatrixWorld(true);

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

  let soleY = Infinity;
  let ankleY = Infinity;
  root.traverse((obj) => {
    const bone = obj as Bone;
    if (!bone.isBone) return;
    bone.getWorldPosition(_solePos);
    if (isSoleBone(bone.name)) {
      soleY = Math.min(soleY, _solePos.y);
    } else if (isFootBone(bone.name)) {
      // Ankle sits slightly above the sole — bias down a little when no ball bone.
      ankleY = Math.min(ankleY, _solePos.y - 0.03);
    }
  });

  const meshMinY = Number.isFinite(_box.min.y) ? _box.min.y : 0;
  const boneSole = Number.isFinite(soleY)
    ? soleY
    : Number.isFinite(ankleY)
      ? ankleY
      : meshMinY;
  // Prefer the lower of bone sole and mesh — catches shoe geometry below the bone.
  const groundY = Math.min(boneSole, meshMinY);
  // Idle/combat poses lift the balls a few cm vs bind pose; sink slightly so
  // soles read as planted instead of hovering above cobble.
  const SOLE_PLANT_SINK = 0.08;
  const yOffset = Number.isFinite(groundY) ? -groundY - SOLE_PLANT_SINK : 0;

  return {
    yOffset,
    soleY: groundY,
    box: {
      min: { x: _box.min.x, y: _box.min.y, z: _box.min.z },
      max: { x: _box.max.x, y: _box.max.y, z: _box.max.z },
    },
  };
}
